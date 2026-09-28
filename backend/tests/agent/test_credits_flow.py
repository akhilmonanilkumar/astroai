"""Credits through the whole turn graph: free allowances, stating the cost, charging,
refunds on a thumbs-down, balance, and never charging for a failed answer."""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage

from guruji.agent.credits_copy import MONEY
from guruji.astro import Sky
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
    assert r.bubbles == [MONEY["empty"]["hinglish"]]  # no credits, no daily inside welcome

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
    r = await chat.send("❤️", kind="reaction", reply_id=charged_turn.turn_id)
    assert r.bubbles == []

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
