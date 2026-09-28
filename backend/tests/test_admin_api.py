"""Admin console API: sign-in, inbox and team replies, users, config, audit."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from guruji.admin.app import create_admin_app, normalize_phone
from guruji.admin.auth import AuthError, DevVerifier, Identity, SupabaseVerifier
from guruji.appconfig import DEFAULTS, ConfigError, ConfigReader, validate
from guruji.config import Settings
from guruji.crypto import FieldCipher, decode_key, lookup_hash
from guruji.db.models import EncryptedBirth, EscalationOpen, InboundLog, TurnWrite
from guruji.db.store import MemoryStore
from guruji.queue.streams import Queue
from guruji.safety.messages import TEAM_LABEL
from guruji.sender.worker import SendHandler
from guruji.whatsapp.client import WhatsAppClient

WA = "919800000001"
OWNER = {"Authorization": "Bearer dev:owner@team.in"}
AGENT = {"Authorization": "Bearer dev:agent@team.in:agent"}


def cid() -> str:
    return uuid.uuid4().hex


class Clock:
    def __init__(self) -> None:
        self.at = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.at


class Env:
    def __init__(self, settings: Settings, redis: Any) -> None:
        self.settings = settings
        self.redis = redis
        self.store = MemoryStore()
        self.clock = Clock()
        self.cipher = FieldCipher(decode_key(settings.field_encryption_key))
        app = create_admin_app(settings, self.store, redis, DevVerifier(), clock=self.clock)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://admin"
        )

    async def user(self, wa_id: str = WA, **kw: Any) -> str:
        key = decode_key(self.settings.lookup_hmac_key)
        user, _ = await self.store.get_or_create_user(lookup_hash(key, wa_id))
        await self.store.set_wa_id(user.id, self.cipher.encrypt("wa_id", wa_id, user.id))
        await self.store.commit_turn(
            TurnWrite(user.id, f"t-{uuid.uuid4().hex}", [InboundLog(cid(), "text", "hi")], **kw)
        )
        return user.id

    async def escalated(self, language: str = "hinglish") -> tuple[str, str]:
        esc = EscalationOpen(str(uuid.uuid4()), "human_requested", 3, "active")
        user_id = await self.user(
            state="escalated", language=language, escalation=esc, reply_body="ok"
        )
        return user_id, esc.id

    async def sent(self) -> list[dict[str, Any]]:
        return [json.loads(f["job"]) for _, f in await self.redis.xrange("q:send")]

    def actions(self) -> list[str]:
        return [a.action for a in self.store.audit_log]


@pytest.fixture
async def env(settings: Settings, redis: Any) -> Any:
    e = Env(settings, redis)
    yield e
    await e.http.aclose()


# --- sign-in ---------------------------------------------------------------------------


async def test_every_route_needs_a_team_member(env: Env) -> None:
    assert (await env.http.get("/api/me")).status_code == 401
    bad = await env.http.get("/api/me", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    me = await env.http.get("/api/me", headers=OWNER)
    assert me.json()["email"] == "owner@team.in" and me.json()["role"] == "owner"
    agent = await env.http.get("/api/me", headers=AGENT)
    assert agent.json()["role"] == "agent"


class _FixedVerifier:
    def __init__(self, identity: Identity) -> None:
        self.identity = identity

    async def verify(self, token: str) -> Identity:
        return self.identity


async def test_mfa_and_membership_are_required(settings: Settings, redis: Any) -> None:
    store = MemoryStore()
    await store.add_admin("member@team.in", "agent")

    async def status(identity: Identity) -> tuple[int, str]:
        app = create_admin_app(settings, store, redis, _FixedVerifier(identity))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            r = await c.get("/api/me", headers={"Authorization": "Bearer x"})
            return r.status_code, r.json().get("detail", "")

    assert await status(Identity("member@team.in", mfa=False)) == (401, "mfa_required")
    assert await status(Identity("stranger@x.in", mfa=True)) == (403, "not_a_team_member")
    assert (await status(Identity("member@team.in", mfa=True)))[0] == 200


def _supabase_token(secret: str, **claims: Any) -> str:
    base = {
        "sub": "u1",
        "email": "Member@Team.in",
        "aud": "authenticated",
        "iss": "https://proj.supabase.co/auth/v1",
        "exp": int(time.time()) + 600,
        "aal": "aal2",
    }
    return jwt.encode({**base, **claims}, secret, algorithm="HS256")


async def test_supabase_tokens() -> None:
    secret = "s" * 32
    v = SupabaseVerifier("https://proj.supabase.co", secret)
    assert await v.verify(_supabase_token(secret)) == Identity("member@team.in", mfa=True)
    one_factor = await v.verify(_supabase_token(secret, aal="aal1"))
    assert not one_factor.mfa
    for bad in (
        _supabase_token("wrong" * 8),
        _supabase_token(secret, exp=int(time.time()) - 10),
        _supabase_token(secret, aud="anon"),
        _supabase_token(secret, iss="https://other.supabase.co/auth/v1"),
    ):
        with pytest.raises(AuthError):
            await v.verify(bad)


async def test_dev_sign_in_is_refused_outside_dev() -> None:
    with pytest.raises(ValueError, match="dev admin sign-in"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="prod",
            redis_url="redis://r",
            database_url="postgresql://db",
            wa_verify_token="a",
            wa_app_secret="b",
            wa_access_token="c",
            field_encryption_key="Ag" + "A" * 41 + "=",
            lookup_hmac_key="Aw" + "A" * 41 + "=",
            guru_model="sarvam:x",
            talk_model="sarvam:x",
            fast_model="sarvam:x",
            admin_auth="dev",
        )


# --- inbox and team replies ---------------------------------------------------------------


async def test_team_reply_goes_out_labelled_and_once(env: Env) -> None:
    user_id, esc_id = await env.escalated("hinglish")
    [item] = (await env.http.get("/api/escalations", headers=AGENT)).json()
    assert item["id"] == esc_id and item["window_open"] and item["status"] == "open"

    body = {"client_id": cid(), "text": "Namaste, main Asha hoon, team se."}
    r = await env.http.post(f"/api/escalations/{esc_id}/reply", json=body, headers=AGENT)
    assert r.status_code == 200
    again = await env.http.post(f"/api/escalations/{esc_id}/reply", json=body, headers=AGENT)
    assert again.status_code == 200  # a retried send is not sent twice

    [job] = await env.sent()
    expected = f"{TEAM_LABEL['hinglish']}\nNamaste, main Asha hoon, team se."
    assert (job["kind"], job["to"], job["bubbles"]) == ("bubbles", WA, [expected])
    last = env.store.messages[user_id][-1]
    assert (last.sent_by, last.body, (last.meta or {})["kind"]) == ("human", expected, "human")
    esc = await env.store.get_escalation(esc_id)
    assert esc is not None and esc.status == "acknowledged"  # replying takes the case
    assert env.actions() == ["human_reply"]


async def test_after_24_hours_only_the_template(env: Env) -> None:
    _, esc_id = await env.escalated("hi")
    env.clock.at += timedelta(hours=25)
    [item] = (await env.http.get("/api/escalations", headers=AGENT)).json()
    assert not item["window_open"]
    r = await env.http.post(
        f"/api/escalations/{esc_id}/reply", json={"client_id": cid(), "text": "hi"}, headers=AGENT
    )
    assert (r.status_code, r.json()["detail"]) == (409, "window_closed")
    r = await env.http.post(
        f"/api/escalations/{esc_id}/template", json={"client_id": cid()}, headers=AGENT
    )
    assert r.status_code == 200
    [job] = await env.sent()
    assert (job["kind"], job["name"], job["language"]) == ("template", "team_followup", "hi")


async def test_close_hands_back_and_stops_replies(env: Env) -> None:
    user_id, esc_id = await env.escalated()
    r = await env.http.post(
        f"/api/escalations/{esc_id}/close",
        json={"hand_back": True, "note": "calmer now"},
        headers=AGENT,
    )
    assert r.json()["status"] == "handed_back"
    assert env.store.users[user_id].state == "active"
    reply = await env.http.post(
        f"/api/escalations/{esc_id}/reply", json={"client_id": cid(), "text": "x"}, headers=AGENT
    )
    assert reply.status_code == 409
    closed = (await env.http.get("/api/escalations?status=closed", headers=AGENT)).json()
    assert [c["notes"] for c in closed] == ["calmer now"]
    assert (await env.http.get("/api/escalations/not-a-uuid", headers=AGENT)).status_code == 404


async def test_acknowledge(env: Env) -> None:
    _, esc_id = await env.escalated()
    r = await env.http.post(f"/api/escalations/{esc_id}/ack", headers=AGENT)
    assert r.json()["status"] == "acknowledged"
    esc = await env.store.get_escalation(esc_id)
    assert esc is not None and esc.acknowledged_at is not None


# --- feedback ---------------------------------------------------------------------------


async def test_feedback_shows_rated_answers_and_audits_each_user(env: Env) -> None:
    user_id = await env.user(state="active", language="hinglish")
    await env.store.commit_turn(
        TurnWrite(
            user_id,
            "t-q",
            [InboundLog("w-q", "text", "shaadi kab hogi?")],
            reply_body="2027 mein yog hain.",
        )
    )
    await env.store.commit_turn(
        TurnWrite(user_id, "t-r", [InboundLog("w-r", "reaction", "👎")], feedback=("t-q", "down"))
    )
    rows = (await env.http.get("/api/feedback", params={"rating": "down"}, headers=AGENT)).json()
    [row] = rows
    assert (row["question"], row["answer"], row["rating"]) == (
        "shaadi kab hogi?",
        "2027 mein yog hain.",
        "down",
    )
    assert (
        await env.http.get("/api/feedback", params={"rating": "up"}, headers=AGENT)
    ).json() == []
    assert env.actions() == ["view_feedback"]


# --- users ----------------------------------------------------------------------------


async def test_find_by_phone_and_views_are_audited(env: Env) -> None:
    user_id = await env.user(state="active")
    await env.user("919800000002")
    for phone in ("98000 00001", "+91 98000-00001"):
        found = (await env.http.get("/api/users", params={"phone": phone}, headers=AGENT)).json()
        assert [u["id"] for u in found] == [user_id]
    assert (
        await env.http.get("/api/users", params={"phone": "12"}, headers=AGENT)
    ).status_code == 422
    assert len((await env.http.get("/api/users", headers=AGENT)).json()) == 2

    for _ in range(2):  # polling: counted once
        assert (await env.http.get(f"/api/users/{user_id}", headers=AGENT)).status_code == 200
        await env.http.get(f"/api/users/{user_id}/messages", headers=AGENT)
    assert env.actions() == ["search_phone", "search_phone", "view_user", "view_messages"]
    assert all("98000" not in json.dumps(a.detail) for a in env.store.audit_log)


async def test_birth_details_are_masked_until_an_owner_reveals(env: Env) -> None:
    key = decode_key(env.settings.lookup_hmac_key)
    user, _ = await env.store.get_or_create_user(lookup_hash(key, WA))
    enc = env.cipher.encrypt
    await env.store.set_wa_id(user.id, enc("wa_id", WA, user.id))
    birth = EncryptedBirth(
        enc("name", "Priya", user.id),
        enc("birth_date", "1990-07-15", user.id),
        enc("birth_time", "09:00", user.id),
        True,
        enc("place_label", "Pune, Maharashtra, India", user.id),
        enc("latitude", "18.52", user.id),
        enc("longitude", "73.85", user.id),
        "Asia/Kolkata",
    )
    await env.store.commit_turn(TurnWrite(user.id, "t", [], birth=birth, state="active"))

    view = (await env.http.get(f"/api/users/{user.id}", headers=AGENT)).text
    assert "Priya" not in view and "1990" not in view and "Pune" not in view
    assert json.loads(view)["birth"] == {
        "on_file": True,
        "name_on_file": True,
        "time_known": True,
        "tz_name": "Asia/Kolkata",
    }
    assert (await env.http.post(f"/api/users/{user.id}/reveal", headers=AGENT)).status_code == 403
    shown = (await env.http.post(f"/api/users/{user.id}/reveal", headers=OWNER)).json()
    assert (shown["phone"], shown["name"], shown["birth_date"]) == (WA, "Priya", "1990-07-15")
    assert "reveal_personal_data" in env.actions()


async def test_block_unblock_and_credits_are_owner_only(env: Env) -> None:
    user_id = await env.user(state="active")
    assert (await env.http.post(f"/api/users/{user_id}/block", headers=AGENT)).status_code == 403
    r = await env.http.post(f"/api/users/{user_id}/block", headers=OWNER)
    assert r.json() == {"state": "blocked"}
    r = await env.http.post(f"/api/users/{user_id}/unblock", headers=OWNER)
    assert r.json() == {"state": "new"}  # never onboarded: starts over

    body = {"client_id": cid(), "delta": 3, "note": "refund for a failed answer"}
    r = await env.http.post(f"/api/users/{user_id}/credits", json=body, headers=OWNER)
    assert r.json() == {"balance": 3, "applied": True}
    r = await env.http.post(f"/api/users/{user_id}/credits", json=body, headers=OWNER)
    assert r.json() == {"balance": 3, "applied": False}
    zero = {**body, "client_id": cid(), "delta": 0}
    assert (
        await env.http.post(f"/api/users/{user_id}/credits", json=zero, headers=OWNER)
    ).status_code == 422
    assert env.actions() == ["block", "unblock", "credit_adjust"]


async def test_escalated_users_cannot_be_blocked(env: Env) -> None:
    user_id, _ = await env.escalated()
    r = await env.http.post(f"/api/users/{user_id}/block", headers=OWNER)
    assert r.status_code == 409


# --- config, metrics, audit ------------------------------------------------------------


async def test_config_is_validated_and_audited(env: Env) -> None:
    keys = [c["key"] for c in (await env.http.get("/api/config", headers=AGENT)).json()]
    assert set(keys) == set(DEFAULTS)
    flags = {**DEFAULTS["flags"], "voice_enabled": False}
    r = await env.http.put("/api/config/flags", json={"value": flags}, headers=AGENT)
    assert r.status_code == 403
    r = await env.http.put(
        "/api/config/flags", json={"value": {**flags, "surprise": 1}}, headers=OWNER
    )
    assert r.status_code == 422 and "surprise" in r.json()["detail"]
    r = await env.http.put("/api/config/flags", json={"value": flags}, headers=OWNER)
    assert r.status_code == 200
    assert (await ConfigReader(env.store).flags()).voice_enabled is False
    [entry] = env.store.audit_log
    assert entry.detail == {"key": "flags", "before": DEFAULTS["flags"], "after": flags}
    unknown = await env.http.put("/api/config/nope", json={"value": 1}, headers=OWNER)
    assert unknown.status_code == 422

    audit = await env.http.get("/api/audit", headers=OWNER)
    assert [a["action"] for a in audit.json()] == ["config_update"]
    assert (await env.http.get("/api/audit", headers=AGENT)).status_code == 403


async def test_metrics(env: Env) -> None:
    await env.escalated()
    m = (await env.http.get("/api/metrics", params={"days": 7}, headers=AGENT)).json()
    assert m["funnel"]["started"] == 1 and len(m["days"]) == 7
    assert m["open_escalations"] == {"human_requested": 1}


# --- pieces ------------------------------------------------------------------------------


def test_config_validation() -> None:
    assert validate("prashna", {"followups": 3, "voice_credit_cost": 2}) == DEFAULTS["prashna"]
    with pytest.raises(ConfigError, match="unique"):
        validate("packs", [DEFAULTS["packs"][0], DEFAULTS["packs"][0]])
    with pytest.raises(ConfigError, match="hinglish"):
        validate("human_template", {"name": "t", "languages": {"en": "en", "hi": "hi"}})
    with pytest.raises(ConfigError, match="price_inr"):
        validate("passes", [{"id": "x", "price_inr": 0, "days": 30}])


async def test_config_reader_falls_back_on_a_bad_row() -> None:
    store = MemoryStore()
    await store.set_config("flags", {"voice_enabled": "maybe?"}, "migration")
    assert (await ConfigReader(store).flags()).voice_enabled is True


def test_normalize_phone() -> None:
    assert normalize_phone("98000 00001") == "919800000001"
    assert normalize_phone("+1 (415) 555-0100") == "14155550100"
    assert normalize_phone("123") is None


async def test_sender_sends_a_template_once(settings: Settings, redis: Any) -> None:
    calls: list[dict[str, Any]] = []

    def graph(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(calls)}"}]})

    client = WhatsAppClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    job = {"kind": "template", "to": WA, "turn_id": "h_1", "name": "team_followup"}
    job["language"] = "hi"
    handler = SendHandler(redis, settings, client)
    await handler(Queue.SEND, job)
    await handler(Queue.SEND, job)  # redelivery
    assert len(calls) == 1
    assert calls[0]["template"] == {"name": "team_followup", "language": {"code": "hi"}}


async def test_erase_is_owner_only_and_stops_team_messages(env: Env) -> None:
    user_id, esc_id = await env.escalated()
    env.store.users[user_id].state = "opted_out"  # they sent STOP during the case
    r = await env.http.post(
        f"/api/escalations/{esc_id}/reply", json={"client_id": cid(), "text": "hi"}, headers=AGENT
    )
    assert (r.status_code, r.json()["detail"]) == (409, "opted_out")
    assert (await env.http.post(f"/api/users/{user_id}/erase", headers=AGENT)).status_code == 403
    assert (await env.http.post(f"/api/users/{user_id}/erase", headers=OWNER)).json() == {
        "erased": True
    }
    again = await env.http.post(f"/api/users/{user_id}/erase", headers=OWNER)
    assert again.status_code == 409
    assert "erase_user" in env.actions()


async def test_payment_issues_are_listed_and_settled_once(env: Env) -> None:
    from guruji.db.models import Order

    user_id = await env.user()
    await env.store.create_order(Order("gj-i", user_id, "pack", "p51", 5100, prashnas=10))
    await env.store.record_payment_issue(
        "duplicate:pay_x", "duplicate", "pay_x", reference_id="gj-i", amount_paise=5100, details={}
    )
    [issue] = (await env.http.get("/api/payment-issues", headers=AGENT)).json()
    assert (issue["kind"], issue["user_id"]) == ("duplicate", user_id)
    url = f"/api/payment-issues/{issue['id']}/resolve"
    assert (await env.http.post(url, headers=AGENT)).status_code == 200
    assert (await env.http.post(url, headers=AGENT)).status_code == 409
    assert (await env.http.get("/api/payment-issues", headers=AGENT)).json() == []
    all_ = (await env.http.get("/api/payment-issues?status=all", headers=AGENT)).json()
    assert all_[0]["resolved_by"].startswith("admin:")
