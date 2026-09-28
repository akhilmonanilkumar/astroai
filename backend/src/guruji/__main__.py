"""Role entrypoint: every box runs the same image with a different role.

    python -m guruji <role>

Roles: ingress, coalescer, turn, sender, alerts, admin (console API), jobs (payment checks
and background jobs), simulator, dev (all of them in one process).

One-off commands: fetch-ephemeris (JPL ephemeris for the astro engine), fetch-geonames
(places for onboarding), fetch-models (the local embedding model),
`add-admin <email> [--role owner|agent]` (let a team member into the admin console),
`resolve-escalation <id> [--hand-back]` (the console does this too),
`eval --models A,B [--judge M]` (guru regression evals / model bake-off) and
`models [sarvam]` (list the models the provider serves) and
`loadtest --users N --messages M` (through the simulator; see guruji.loadtest).
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from guruji.config import Settings, get_settings
from guruji.db import Store, open_store
from guruji.ingress.app import create_app as create_ingress
from guruji.ingress.coalescer import run_coalescer
from guruji.logs import setup_logging
from guruji.queue.streams import Queue, Worker
from guruji.redis_conn import make_redis
from guruji.sender.worker import SendHandler
from guruji.turn.worker import TurnHandler
from guruji.whatsapp.client import WhatsAppClient

if TYPE_CHECKING:
    from guruji.agent.graph import GuruResponder
    from guruji.rag.retrieve import Retriever
    from guruji.voice.speech import SarvamSpeech

log = logging.getLogger("guruji")

ROLES = ("ingress", "coalescer", "turn", "sender", "alerts", "admin", "jobs", "simulator", "dev")
COMMANDS = (
    "fetch-ephemeris",
    "fetch-geonames",
    "fetch-models",
    "resolve-escalation",
    "add-admin",
    "eval",
    "models",
    "loadtest",
)


def _consumer_name(role: str) -> str:
    return f"{role}-{socket.gethostname()}-{os.getpid()}"


async def _serve(app: FastAPI, settings: Settings, port: int, stop: asyncio.Event) -> None:
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.bind_host, port=port, log_level="warning")
    )
    # uvicorn captures SIGINT/SIGTERM itself while serving; whichever side sees the
    # shutdown first (server exit or our stop event) brings the whole process down.
    served = asyncio.create_task(server.serve())
    stopped = asyncio.create_task(stop.wait())
    await asyncio.wait({served, stopped}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    stop.set()
    await served
    stopped.cancel()


def _block_ms(settings: Settings) -> int:
    return 0 if settings.redis_url.startswith("memory://") else 1000


def _speech(settings: Settings) -> "SarvamSpeech | None":
    from guruji.voice.speech import SarvamSpeech

    if not settings.voice_enabled or settings.sarvam_api_key is None:
        return None
    return SarvamSpeech(settings.sarvam_api_key, settings.tts_speaker, settings.sarvam_speech_url)


async def _guru_responder(
    settings: Settings, store: Store, client: WhatsAppClient
) -> "GuruResponder":
    from guruji.agent.graph import GuruResponder
    from guruji.agent.llm import cache_prompt_blocks, fast_model, reading_models, talk_models
    from guruji.ephemeris import EphemerisMissingError, load_sky
    from guruji.geo.places import load_places

    try:
        sky = load_sky(settings.ephemeris_path)
        places = await asyncio.to_thread(load_places, settings.geonames_dir)
    except (EphemerisMissingError, FileNotFoundError) as e:
        raise SystemExit(
            f"{e}. Run once: uv run python -m guruji fetch-ephemeris && "
            "uv run python -m guruji fetch-geonames && uv run python -m guruji fetch-models"
        ) from e
    retriever = await _retriever(settings, store)
    return GuruResponder(
        settings,
        store,
        sky,
        places,
        reading_models(settings),
        fast_model(settings),
        retriever,
        talk_models=talk_models(settings),
        cache_blocks=cache_prompt_blocks(settings),
        speech=_speech(settings),
        media=client,
    )


async def _retriever(settings: Settings, store: Store) -> "Retriever":
    from guruji.db.postgres import PostgresStore
    from guruji.rag.cards import load_cards
    from guruji.rag.embed import FastEmbedder
    from guruji.rag.index import CardIndex, MemoryCardIndex, PostgresCardIndex, sync_cards
    from guruji.rag.retrieve import Retriever

    cards = load_cards()
    embedder = await asyncio.to_thread(FastEmbedder, settings.models_dir)
    index: CardIndex
    if isinstance(store, PostgresStore):
        written = await sync_cards(store.pool, cards, embedder)
        log.info("rule cards synced: %d of %d updated", written, len(cards))
        index = PostgresCardIndex(store.pool)
    else:
        index = await asyncio.to_thread(MemoryCardIndex, cards, embedder)
    return Retriever(index, embedder)


async def _turn(redis: Redis, settings: Settings, store: Store, stop: asyncio.Event) -> None:
    client = WhatsAppClient(settings)  # downloads inbound voice notes
    try:
        responder = await _guru_responder(settings, store, client)
        handler = TurnHandler(redis, settings, responder)
        await _run_turn_worker(redis, settings, handler, stop)
    finally:
        await client.aclose()


async def _run_turn_worker(
    redis: Redis, settings: Settings, handler: TurnHandler, stop: asyncio.Event
) -> None:
    worker = Worker(
        redis,
        [Queue.CRISIS, Queue.TURN],
        handler,
        consumer=_consumer_name("turn"),
        concurrency=settings.worker_concurrency,
        max_attempts=settings.job_max_attempts,
        block_ms=_block_ms(settings),
    )
    await worker.run(stop)


async def _sender(redis: Redis, settings: Settings, stop: asyncio.Event) -> None:
    client = WhatsAppClient(settings)
    try:
        worker = Worker(
            redis,
            [Queue.SEND],
            SendHandler(redis, settings, client, _speech(settings)),
            consumer=_consumer_name("sender"),
            concurrency=settings.worker_concurrency,
            max_attempts=settings.job_max_attempts,
            block_ms=_block_ms(settings),
        )
        await worker.run(stop)
    finally:
        await client.aclose()


async def _alerts(redis: Redis, settings: Settings, store: Store, stop: asyncio.Event) -> None:
    from guruji.alerts.worker import (
        Alerter,
        AlertHandler,
        LogAlerter,
        TelegramAlerter,
        run_repinger,
    )

    alerter: Alerter
    if settings.telegram_bot_token and settings.telegram_chat_id:
        alerter = TelegramAlerter(
            settings.telegram_bot_token.get_secret_value(),
            settings.telegram_chat_id,
            settings.admin_console_url,
        )
    else:
        if settings.env in ("staging", "prod"):
            raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required outside dev")
        alerter = LogAlerter(settings.admin_console_url)
    worker = Worker(
        redis,
        [Queue.ALERT],
        AlertHandler(store, alerter),
        consumer=_consumer_name("alerts"),
        concurrency=4,
        max_attempts=settings.job_max_attempts,
        block_ms=_block_ms(settings),
    )
    await asyncio.gather(worker.run(stop), run_repinger(redis, store, settings, stop))


async def _jobs(redis: Redis, settings: Settings, store: Store, stop: asyncio.Event) -> None:
    from guruji.billing.razorpay import Razorpay
    from guruji.billing.worker import PaymentHandler
    from guruji.jobs import BackgroundHandler, Capi, MetaCapi, run_retention

    razorpay = Razorpay(
        settings.razorpay_key_id,
        settings.razorpay_key_secret.get_secret_value(),
        settings.razorpay_api_base,
    )
    capi: Capi | None = (
        MetaCapi(settings) if settings.capi_dataset_id and settings.capi_access_token else None
    )
    whatsapp = WhatsAppClient(settings)  # data exports go out as documents
    payments = PaymentHandler(redis, settings, store, razorpay)
    background = BackgroundHandler(settings, store, capi, whatsapp)

    async def handle(queue: Queue, job: dict[str, Any]) -> None:
        await (payments if queue == Queue.PAYMENT else background)(queue, job)

    worker = Worker(
        redis,
        [Queue.PAYMENT, Queue.BACKGROUND],
        handle,
        consumer=_consumer_name("jobs"),
        concurrency=8,
        max_attempts=settings.job_max_attempts,
        block_ms=_block_ms(settings),
    )
    try:
        await asyncio.gather(worker.run(stop), run_retention(redis, store, stop))
    finally:
        await razorpay.aclose()
        await whatsapp.aclose()


def _admin_app(settings: Settings, store: Store, redis: Redis) -> FastAPI:
    from guruji.admin.app import create_admin_app
    from guruji.admin.auth import make_verifier

    return create_admin_app(settings, store, redis, make_verifier(settings))


async def _check_admin_schema(store: Store) -> None:
    """Fail at startup, not on every request, when the M6 migration was never applied."""
    from guruji.db.postgres import PostgresStore

    if isinstance(store, PostgresStore) and await store.missing_tables(["admins"]):
        raise SystemExit(
            "the database has no `admins` table: apply "
            "supabase/migrations/20261001090000_admin_console.sql (in docker compose, "
            "`docker compose down -v` re-runs all migrations on the next start)"
        )


def _simulator_app(settings: Settings) -> FastAPI:
    if settings.env not in ("dev", "test"):
        raise SystemExit("the simulator role is dev-only")
    from guruji.simulator.app import create_app

    return create_app(settings)


async def run_role(role: str, settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):  # signal.signal also works on Windows
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    redis = make_redis(settings.redis_url)
    # One store per process: in `dev` the turn and alerts roles share the in-memory store.
    store = (
        await open_store(settings.database_url, settings.db_pool_max)
        if role in ("turn", "alerts", "admin", "jobs", "dev")
        else None
    )
    jobs = []
    if role in ("ingress", "dev"):
        jobs.append(_serve(create_ingress(settings, redis), settings, settings.ingress_port, stop))
    if role in ("coalescer", "dev"):
        jobs.append(run_coalescer(redis, settings, stop))
    if role in ("turn", "dev"):
        assert store is not None
        jobs.append(_turn(redis, settings, store, stop))
    if role in ("alerts", "dev"):
        assert store is not None
        jobs.append(_alerts(redis, settings, store, stop))
    if role in ("sender", "dev"):
        jobs.append(_sender(redis, settings, stop))
    if role in ("jobs", "dev"):
        assert store is not None
        jobs.append(_jobs(redis, settings, store, stop))
    if role in ("admin", "dev"):
        assert store is not None
        await _check_admin_schema(store)
        app = _admin_app(settings, store, redis)
        jobs.append(_serve(app, settings, settings.admin_port, stop))
    if role in ("simulator", "dev"):
        jobs.append(_serve(_simulator_app(settings), settings, settings.simulator_port, stop))

    log.info("starting role=%s env=%s", role, settings.env)
    if role in ("simulator", "dev"):
        log.info("simulator: http://%s:%d", settings.bind_host, settings.simulator_port)
    if role in ("admin", "dev"):
        log.info("admin API: http://%s:%d", settings.bind_host, settings.admin_port)
    try:
        await asyncio.gather(*jobs)
    finally:
        if store is not None:
            await store.aclose()
        await redis.aclose()


async def _resolve(settings: Settings, escalation_id: str, hand_back: bool) -> None:
    store = await open_store(settings.database_url, settings.db_pool_max)
    try:
        ok = await store.close_escalation(
            escalation_id, hand_back=hand_back, by="cli", note="closed from the command line"
        )
    finally:
        await store.aclose()
    if not ok:
        raise SystemExit(f"escalation {escalation_id} is not open")
    log.info("escalation %s %s", escalation_id, "handed back" if hand_back else "resolved")


async def _add_admin(settings: Settings, email: str, role: str) -> None:
    if settings.database_url.startswith("memory://"):
        raise SystemExit("add-admin needs DATABASE_URL (in dev, sign in as dev:<email> instead)")
    store = await open_store(settings.database_url, settings.db_pool_max)
    try:
        admin = await store.add_admin(email, "owner" if role == "owner" else "agent")
    finally:
        await store.aclose()
    log.info("admin %s is now %s", admin.id, admin.role)


async def _eval(settings: Settings, args: argparse.Namespace) -> None:
    from pathlib import Path

    from guruji.ephemeris import load_sky
    from guruji.evals.runner import DEFAULT_OUT, require_model, run_eval, summary, write_report

    names = [m.strip() for m in (args.models or settings.guru_model).split(",") if m.strip()]
    models = {n: require_model(n, settings) for n in names}
    judge = require_model(args.judge, settings) if args.judge else None
    retriever = None
    if not args.no_rag:
        store = await open_store("memory://")
        try:
            retriever = await _retriever(settings, store)
        except Exception as e:  # no local embedding model: evaluate without rule cards
            log.warning("rule cards off (%s); run fetch-models to include them", e)
    answers = await run_eval(
        settings,
        load_sky(settings.ephemeris_path),
        models,
        judge_model=judge,
        retriever=retriever,
        only=set(args.only.split(",")) if args.only else None,
        limit=args.limit,
        concurrency=args.concurrency,
    )
    print(summary(answers))
    print(f"\nreport: {write_report(answers, Path(args.out or DEFAULT_OUT))}")


def _list_models(settings: Settings, provider: str) -> None:
    import httpx

    keys = {
        "sarvam": (settings.sarvam_base_url, settings.sarvam_api_key),
    }
    if provider not in keys:
        raise SystemExit(f"models: provider must be one of {sorted(keys)}")
    base, key = keys[provider]
    if key is None:
        raise SystemExit(f"set {provider.upper()}_API_KEY first")
    r = httpx.get(
        f"{base.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        timeout=20,
    )
    r.raise_for_status()
    for m in sorted(r.json().get("data", []), key=lambda m: str(m.get("id"))):
        print(f"{provider}:{m.get('id')}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="guruji")
    parser.add_argument("role", choices=ROLES + COMMANDS)
    parser.add_argument("target", nargs="?", help="escalation id, or email for add-admin")
    parser.add_argument("--hand-back", action="store_true", help="give the chat back to Guruji")
    parser.add_argument("--models", help="eval: comma-separated provider:model list")
    parser.add_argument("--judge", help="eval: model that rates naturalness")
    parser.add_argument("--only", help="eval: comma-separated case ids")
    parser.add_argument("--limit", type=int, help="eval: first N cases")
    parser.add_argument(
        "--concurrency", type=int, default=4, help="eval: parallel answers; loadtest: users"
    )
    parser.add_argument("--no-rag", action="store_true", help="eval: without rule cards")
    parser.add_argument("--out", help="eval: report directory")
    parser.add_argument("--users", type=int, default=50, help="loadtest: synthetic users")
    parser.add_argument("--messages", type=int, default=3, help="loadtest: messages per user")
    parser.add_argument("--simulator", default=None, help="loadtest: simulator URL")
    parser.add_argument(
        "--role", dest="admin_role", choices=("owner", "agent"), default="agent", help="add-admin"
    )
    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.log_level)
    if args.role == "fetch-ephemeris":
        from guruji.ephemeris import fetch_ephemeris

        log.info("ephemeris ready at %s", fetch_ephemeris(settings.ephemeris_path))
        return
    if args.role == "fetch-models":
        from guruji.rag.embed import fetch_model

        fetch_model(settings.models_dir)
        return
    if args.role == "fetch-geonames":
        from guruji.geo.places import fetch_geonames

        log.info("places ready in %s", fetch_geonames(settings.geonames_dir))
        return
    # psycopg's async mode needs a selector loop; Windows defaults to Proactor.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    if args.role == "resolve-escalation":
        if not args.target:
            parser.error("resolve-escalation needs an escalation id")
        asyncio.run(_resolve(settings, args.target, args.hand_back), loop_factory=loop_factory)
        return
    if args.role == "loadtest":
        from guruji.loadtest import report, run

        sim = args.simulator or f"http://{settings.bind_host}:{settings.simulator_port}"
        result = asyncio.run(
            run(sim, users=args.users, messages=args.messages, concurrency=args.concurrency)
        )
        print(report(result))
        return
    if args.role == "models":
        _list_models(settings, args.target or "sarvam")
        return
    if args.role == "eval":
        asyncio.run(_eval(settings, args), loop_factory=loop_factory)
        return
    if args.role == "add-admin":
        if not args.target or "@" not in args.target:
            parser.error("add-admin needs an email address")
        asyncio.run(_add_admin(settings, args.target, args.admin_role), loop_factory=loop_factory)
        return
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_role(args.role, settings), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
