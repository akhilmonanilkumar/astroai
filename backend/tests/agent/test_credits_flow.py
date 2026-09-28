"""Credits through the whole turn graph: free allowances, stating the cost, charging,
refunds on a thumbs-down, balance, and never charging for a failed answer."""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage

from guruji.agent.credits_copy import MONEY
from guruji.astro import Sky
from guruji.billing.razorpay import RazorpayError
from guruji.config import Settings
from guruji.geo.places import PlaceIndex
from guruji.safety.messages import SAFE_FALLBACK

from .test_conversation import FIRST_READING, NOW, Chat, _onboard, _responder


class Clock:
    def __init__(self) -> None:
        self.at = NOW

    def __call__(self) -> datetime:
        return self.at


def _answers(n: int) -> list[AIMessage]:
    return [AIMessage(f"Jawab {i}: dhairya rakhiye, samay saath dega.") for i in range(n)]


async def test_free_then_ask_then_charge_then_refund(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    clock = Clock()
    responder, store, _ = _responder(
        settings, sky, places, [*FIRST_READING, *_answers(6)], clock=clock
    )
    await store.set_config(
        "free_tier", {"welcome_hours": 72, "welcome_prashnas": 1, "daily_free_answers": 1}, "t"
    )
    await store.set_config(
        "prashna", {"followups": 0, "voice_credit_cost": 2, "followup_hours": 12}, "t"
    )
    chat = Chat(responder)
    await _onboard(chat)
    user = next(iter(store.users.values()))
    user.created_at = NOW  # joined now: inside the welcome window

    r = await chat.send("meri naukri kab lagegi?")
    assert r.bubbles[0].startswith("Jawab 0")  # the welcome prashna
    r = await chat.send("aur shaadi kab hogi?")
    # no credits, and no daily answer inside the welcome window: offer a top-up
    assert r.bubbles[0].startswith(MONEY["empty"]["hinglish"])
    assert r.interactive is not None and r.interactive["type"] == "list"

    clock.at = NOW + timedelta(days=4)  # after the welcome window
    r = await chat.send("shaadi kab hogi?")
    assert r.bubbles[0].startswith("Jawab 1")  # today's free answer

    await store.add_credits(user.id, 3, "adjust", "gift")
    r = await chat.send("ghar kab lenge?")
    assert [b.id for b in r.buttons] == ["spend_yes", "spend_no"]
    assert "1 credit" in r.bubbles[0] and "3" in r.bubbles[0]
    assert await store.balance(user.id) == 3  # nothing spent before a yes

    r = await chat.send("Haan, dekhiye", reply_id="spend_yes")
    assert r.bubbles[0].startswith("Jawab 2") and "1 credit laga · 2 baaki" in r.bubbles[-1]
    assert await store.balance(user.id) == 2
    charged_turn = chat.last_turn
    assert charged_turn is not None

    r = await chat.send("aur videsh yatra?")  # agreed today already: charged straight away
    assert r.bubbles[0].startswith("Jawab 3") and await store.balance(user.id) == 1

    r = await chat.send("👎", kind="reaction", reply_id=charged_turn.turn_id)
    assert "wapas" in r.bubbles[0] and await store.balance(user.id) == 2
    r = await chat.send("👎", kind="reaction", reply_id=charged_turn.turn_id)
    assert r.bubbles == [] and await store.balance(user.id) == 2  # refunded once
    [down] = await store.list_feedback(rating="down")  # kept for persona tuning
    assert down.turn_id == charged_turn.turn_id and down.answer.startswith("Jawab 2")
    r = await chat.send("❤️", kind="reaction", reply_id=charged_turn.turn_id)
    assert r.bubbles == []
    assert [f.rating for f in await store.list_feedback()] == ["up"]  # changed their mind

    r = await chat.send("balance")
    assert r.bubbles[0].startswith("Aapke paas 2 credit")


async def test_not_now_and_failed_answers_cost_nothing(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    clock = Clock()
    broken = AIMessage(
        "Your lifespan looks short in this chart."
    )  # a guardrail break, twice: fallback
    responder, store, _ = _responder(
        settings, sky, places, [*FIRST_READING, broken, broken], clock=clock
    )
    chat = Chat(responder)
    await _onboard(chat)
    user = next(iter(store.users.values()))
    clock.at = user.created_at + timedelta(days=5)
    await store.set_config(
        "free_tier", {"welcome_hours": 72, "welcome_prashnas": 1, "daily_free_answers": 0}, "t"
    )
    await store.add_credits(user.id, 2, "adjust", "gift")

    r = await chat.send("mera career kaisa rahega?")
    assert r.buttons
    r = await chat.send("Abhi nahi", reply_id="spend_no")
    assert r.bubbles == [MONEY["declined"]["hinglish"]]
    assert "pending" not in store.users[user.id].meter

    await chat.send("career kaisa rahega?")
    r = await chat.send("Haan, dekhiye", reply_id="spend_yes")
    assert r.bubbles == [SAFE_FALLBACK["hinglish"]]
    assert await store.balance(user.id) == 2  # a failed answer is free


async def test_recharge_offer_then_checkout(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [*FIRST_READING])
    chat = Chat(responder)
    await _onboard(chat)
    user = next(iter(store.users.values()))

    r = await chat.send("recharge")
    assert r.interactive is not None and r.interactive["type"] == "list"
    ids = [row["id"] for s in r.interactive["action"]["sections"] for row in s["rows"]]
    assert "buy:trial" in ids and "buy:plus_monthly" in ids

    r = await chat.send("₹51 · 10 sawaal", reply_id="buy:p51")
    assert r.interactive is not None and r.interactive["type"] == "order_details"
    ref = r.interactive["action"]["parameters"]["reference_id"]
    order = await store.get_order(ref)
    assert order is not None and (order.user_id, order.amount_paise, order.prashnas) == (
        user.id,
        5100,
        10,
    )
    r = await chat.send("x", reply_id="buy:no-such-pack")
    assert r.interactive is not None and r.interactive["type"] == "list"  # offered again


class FakeLinks:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    async def create_link(
        self, reference_id: str, amount_paise: int, description: str, expire_by: int
    ) -> str:
        if self.fail:
            raise RazorpayError(503, "down")
        self.calls.append((reference_id, amount_paise))
        return f"https://rzp.io/{reference_id}"


async def test_checkout_by_payment_link(settings: Settings, sky: Sky, places: PlaceIndex) -> None:
    links = FakeLinks()
    link_settings = settings.model_copy(update={"payment_checkout": "link"})
    responder, store, _ = _responder(link_settings, sky, places, [*FIRST_READING], links=links)
    chat = Chat(responder)
    await _onboard(chat)

    r = await chat.send("recharge")
    assert r.interactive is not None and "Razorpay" in r.interactive["body"]["text"]
    r = await chat.send("₹51 · 10 sawaal", reply_id="buy:p51")
    assert r.interactive is not None and r.interactive["type"] == "cta_url"
    [(ref, amount)] = links.calls
    assert amount == 5100 and r.payment_check == ref
    assert r.interactive["action"]["parameters"]["url"] == f"https://rzp.io/{ref}"
    order = await store.get_order(ref)
    assert order is not None and order.status == "pending"

    links.fail = True  # Razorpay down: say so, charge nothing, no check to start
    r = await chat.send("₹51 · 10 sawaal", reply_id="buy:p51")
    assert r.interactive is None and r.payment_check is None
    assert "nothing was charged" in r.bubbles[0] or "koi paisa nahi" in r.bubbles[0]
