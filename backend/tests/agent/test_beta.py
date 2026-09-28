"""Closed beta: invite codes before onboarding, a waitlist for everyone else."""

import pytest

from guruji.agent.copy import LINES
from guruji.appconfig import ConfigError, validate
from guruji.astro import Sky
from guruji.config import Settings
from guruji.geo.places import PlaceIndex

from .test_conversation import FIRST_READING, Chat, _ids_of, _onboard, _responder


def test_beta_config_is_validated() -> None:
    assert validate("beta", {"invite_only": True, "codes": ["GURU-BETA"]}) == {
        "invite_only": True,
        "codes": ["GURU-BETA"],
    }
    with pytest.raises(ConfigError, match="needs at least one code"):
        validate("beta", {"invite_only": True, "codes": []})
    with pytest.raises(ConfigError, match="letters, digits or dashes"):
        validate("beta", {"invite_only": False, "codes": ["no spaces"]})


async def test_invite_code_admits_then_onboarding_continues(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [*FIRST_READING])
    await store.set_config("beta", {"invite_only": True, "codes": ["GURU-BETA"]}, "t")
    chat = Chat(responder)

    r = await chat.send("namaste ji")
    assert r.kind == "waitlist" and r.bubbles == [LINES["waitlist"]["hinglish"]]
    user = next(iter(store.users.values()))
    assert store.messages[user.id][0].body is None  # nothing they typed is kept
    r = await chat.send("wrong-code please")
    assert r.kind == "waitlist"

    r = await chat.send("mera code guru-beta hai")  # any case, inside a sentence
    assert _ids_of(r) == ["consent_yes", "consent_notice"]
    assert user.admitted and store.invite_codes[user.id] == "GURU-BETA"
    # the consent tap carries no code: admission is remembered
    r = await chat.send("I agree", reply_id="consent_yes")
    assert _ids_of(r) == ["age_yes", "age_no"]


async def test_consented_users_are_never_waitlisted(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [*FIRST_READING])
    chat = Chat(responder)
    await _onboard(chat)  # joined while the beta was open
    await store.set_config("beta", {"invite_only": True, "codes": ["GURU-BETA"]}, "t")
    responder.config.ttl = 0  # read the new knob now
    r = await chat.send("balance")
    assert r.kind != "waitlist"


async def test_admission_off_waitlists_without_code_and_safety_still_answers(
    settings: Settings, sky: Sky, places: PlaceIndex
) -> None:
    responder, store, _ = _responder(settings, sky, places, [])
    await store.set_config(
        "flags", {"busy_mode": False, "voice_enabled": True, "new_user_admission": False}, "t"
    )
    chat = Chat(responder)
    assert (await chat.send("hello")).kind == "waitlist"
    # help comes first, even for someone not let in
    r = await chat.send("I want to kill myself")
    assert r.kind == "safety" and "14416" in r.body
