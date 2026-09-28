"""DPDP commands through the turn graph: STOP, START, delete my data, export my data."""

import pytest

from guruji.agent.privacy import PRIVACY, detect_privacy
from guruji.astro import Sky
from guruji.config import Settings
from guruji.crypto import decode_key, lookup_hash
from guruji.geo.places import PlaceIndex

from .test_conversation import FIRST_READING, WA_ID, Chat, _onboard, _responder


@pytest.mark.parametrize(
    ("text", "cmd"),
    [
        ("STOP", "stop"),
        ("stop messages", "stop"),
        ("band karo", "stop"),
        ("start", "start"),
        ("delete my data", "delete"),
        ("mera data delete karo", "delete"),
        ("मेरा डेटा डिलीट करो", "delete"),
        ("please erase my details", "delete"),
        ("export my data", "export"),
        ("meri jankari bhejo", "export"),
        ("stop worrying me, what does my seventh house say about marriage", None),
        ("I want to stop my job, is it a good time?", None),
        ("delete karne se pehle bataiye meri shaadi kab hogi aur kaise", None),
        ("namaste", None),
    ],
)
def test_detect(text: str, cmd: str | None) -> None:
    assert detect_privacy(text) == cmd


async def test_stop_start_delete_export(settings: Settings, sky: Sky, places: PlaceIndex) -> None:
    responder, store, _ = _responder(settings, sky, places, [*FIRST_READING])
    chat = Chat(responder)
    await _onboard(chat)
    user = next(iter(store.users.values()))

    r = await chat.send("STOP")
    assert r.bubbles == [PRIVACY["stopped"]["hinglish"]]
    assert user.state == "opted_out" and user.id in store.opted_out_at
    assert (await chat.send("hello?")).bubbles == []  # nothing after STOP
    r = await chat.send("start")
    assert user.state == "active" and user.id not in store.opted_out_at

    r = await chat.send("export my data")
    assert r.tasks == ({"kind": "export", "user_id": user.id, "lang": "hinglish"},)

    r = await chat.send("delete my data")
    assert [b.id for b in r.buttons] == ["erase_yes", "erase_no"]
    r = await chat.send("Nahi, rehne do", reply_id="erase_no")
    assert r.bubbles == [PRIVACY["kept"]["hinglish"]] and user.id not in store.erased

    await chat.send("delete my data")
    r = await chat.send("Haan, sab mitao", reply_id="erase_yes")
    assert r.bubbles == [PRIVACY["deleted"]["hinglish"]]
    assert user.id in store.erased
    assert not store.messages.get(user.id) and not store.births.get(user.id)
    assert not any(k[0] == user.id for k in store.charts)
    wa_hash = lookup_hash(decode_key(settings.lookup_hmac_key), WA_ID)
    assert wa_hash not in store.by_hash  # a new message starts from scratch
    r = await chat.send("namaste")
    assert [b.id for b in r.buttons] == ["consent_yes", "consent_notice"]


async def test_delete_works_before_consent(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [])
    chat = Chat(responder)
    await chat.send("hi")
    r = await chat.send("delete my data")
    assert r.buttons
    await chat.send("Yes, delete all", reply_id="erase_yes")
    assert len(store.erased) == 1
