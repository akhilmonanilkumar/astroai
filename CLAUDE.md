# Guruji (AstroAI Guru)

A personal AI Vedic astrologer on WhatsApp for India. Product decisions live in
`docs/AstroAI-Guru-Product-and-Build-Plan.md`; read it before any product-shaped change.
The build runs in milestones M1–M8 (see "Milestones" below).

## Layout
- `backend/`: Python 3.12, uv. Package `guruji` under `backend/src/guruji/`.
  - `whatsapp/`: Cloud API client, webhook models, signature
  - `ingress/`: FastAPI webhook (verify → dedupe → coalescer). No DB and no business logic here.
  - `queue/`: Redis Streams queues (`streams.py`) and token locks (`locks.py`)
  - `turn/`: turn worker; `Responder` returns a `Reply` (1-2 bubbles + optional buttons)
  - `agent/`: the turn graph (`graph.py`, LangGraph): load → onboarding | guru → one
    idempotent commit. `onboarding.py` is a scripted state machine (consent, 18+, birth
    details, confirm); `extract.py` parses answers (rules first, fast LLM fallback);
    `guru.py` is `create_agent` with the persona (`persona/guruji.md`), the chart prompt
    and deterministic dasha/transit tools; `copy.py` holds onboarding lines in en/hinglish/hi.
    `verify.py` fact-checks every guru reply against the dossier (dashas, dates, placements,
    transits) and sends wrong claims back for a rewrite; what stays wrong is dropped.
    `metering.py` decides who pays for a question (welcome, Guru Plus, daily free, credits);
    `privacy.py` and `commands.py` are the code-answered commands (STOP, delete/export,
    balance, recharge).
  - `db/`: `Store` protocol; `MemoryStore` (dev/test) and `PostgresStore` (psycopg).
    Each turn commits once via `TurnWrite`, keyed by turn_id; replays resend the stored reply.
  - `crypto.py`: per-field AES-GCM for personal data; HMAC lookup hash for phone numbers
  - `sender/`: paced outbound bubbles, typing indicator
  - `simulator/`: fake WhatsApp + Graph API for local dev (dev/test only)
  - `astro/`: Vedic engine (Lahiri, mean node, whole-sign): D1/D9, Vimshottari, panchang, yogas,
    transits, Sade Sati → `compute_dossier()`. Pure; gets a loaded `Sky` from `ephemeris.py`.
    Bump `ENGINE_VERSION` when any stored dossier value would change.
  - `ephemeris.py`: loads/downloads JPL DE440s (1850–2149)
  - `geo/`: offline GeoNames place search (`places.py`) and birth timezones (`tz.py`)
  - `rag/`: rule cards. Corpus in `rag/corpus/*.toml` (original summaries, reviewed in git);
    `factors.py` turns a dossier into keys like `saturn:house-7`; retrieval is factor-first
    (only cards this chart has), ranked by hybrid vector + keyword RRF. Local embeddings
    (MiniLM via fastembed) + a Hinglish/Hindi glossary; pgvector in Postgres, numpy in dev.
  - `safety/`: `detect.py` (crisis/emergency/abuse/human-request rules, en/hinglish/hi,
    run on every message in every state), `messages.py` (scripted replies with Tele-MANAS
    14416 / 112, never generated), `guard.py` (output guardrails on guru replies)
  - `voice/`: Sarvam STT/TTS (`speech.py`); `ogg.py` splits long OGG/Opus voice notes into
    <30 s chunks (Sarvam's real-time limit) without re-encoding. Voice in → voice out.
  - `alerts/`: escalation alerts (Telegram, log in dev), 5-min re-pings of unacknowledged
    crises; acks arrive via ingress `/telegram/webhook` → `alert` queue.
  - `admin/`: the console's API (`admin` role, port 8200). `auth.py` checks Supabase JWTs
    (MFA/aal2 required, email in `admins`); `app.py` serves inbox + team replies, users,
    metrics, config, audit. Store queries for it are the `AdminStore` protocol (`db/admin.py`).
  - `appconfig.py`: typed schemas + defaults for every `app_config` key; `ConfigReader`
    (30 s cache) is how workers read knobs.
  - `billing/`: pack/pass offer and WhatsApp `order_details` checkout (`catalog.py`),
    Razorpay checks and webhook signatures (`razorpay.py`), the payment worker (`worker.py`).
  - `jobs.py`: background jobs (data export, Meta Conversions API) and the hourly
    retention sweep; run by the `jobs` role with payment checks.
  - `evals/`: guru regression questions (`cases.toml`) and the model bake-off runner.
  - `loadtest.py`: load test through the simulator.
- `supabase/migrations/`: SQL schema (RLS on, no policies; backend uses the service role)
- `landing/`: static landing + legal pages (Cloudflare Pages)
- `admin/`: Next.js admin console (Tailwind, shadcn-style components in `components/ui`).
  Client-only pages; `/api/*` is proxied at runtime to the backend admin API, so the browser
  never reaches Supabase tables. Sign-in is Supabase Auth (password + TOTP); `dev:<email>`
  when `SUPABASE_URL` is unset.

## Commands (run from `backend/`)
```
uv sync                         # install
uv run python -m guruji fetch-ephemeris   # once: JPL DE440s (32 MB) into data/ephemeris/
uv run python -m guruji fetch-geonames    # once: GeoNames cities500 (~40 MB) into data/geonames/
uv run python -m guruji fetch-models      # once: embedding model (~0.2 GB) into data/models/
uv run pytest -q                # tests (fakeredis, no Docker needed)
uv run ruff check . && uv run ruff format --check . && uv run mypy
$env:REDIS_URL='memory://'; uv run python -m guruji dev   # everything in one process, no Docker
```
Simulator: http://127.0.0.1:8100. Full stack with real Redis + Postgres: `docker compose up --build`
from the repo root. LLMs are Sarvam (`SARVAM_API_KEY` in backend/.env): `sarvam-105b` (thinking
off) for readings, `sarvam-105b-conversations` for small talk, routing and extraction. Without
a key, dev uses a scripted fake guru.
Postgres store tests run when `TEST_DATABASE_URL` is set (CI sets it); they reset the schema.

Roles (`python -m guruji <role>`): `ingress`, `coalescer`, `turn`, `sender`, `alerts`, `admin`,
`jobs`, `simulator`, `dev`. `python -m guruji add-admin <email> [--role owner|agent]` lets a team member
into the console (they also need a Supabase Auth account). `resolve-escalation <id> [--hand-back]`
closes a case from the CLI; the user returns to the state they were in.

Models: `sarvam:<id>`, `sail:<id>` (GLM, Kimi, DeepSeek on Sail Research; `SAIL_API_KEY`;
`python -m guruji models sail` lists ids) or `anthropic:<id>`. Compare them with
`python -m guruji eval --models A,B --judge C` (report in `tests/evals/output/`, gitignored).
Load test: `python -m guruji loadtest --users 200 --messages 3` against a running stack.
In dev the simulator also plays Razorpay: checkout cards get Pay / Fail buttons.

Admin console (from `admin/`): `npm install`, then `npm run dev` → http://127.0.0.1:3001 against
the admin API of `python -m guruji dev` (sign in as any email; add `:agent` for the agent role).
`npm run typecheck && npm run build` before pushing.

Golden charts: `tests/astro/golden/charts.json` holds 50 charts computed by Swiss Ephemeris; the
engine must match within about 1″. Regenerate with `uv run python tests/astro/make_golden.py`
(dev-only `pysweph`; SE data files go to `data/cache/sweph/`, never committed).

## Rules
- **Astrology maths is deterministic code; the LLM only interprets.** `astro/` is pure (no I/O).
- **Never import `swisseph`/`pyswisseph`/`pysweph` under `src/`** (AGPL). It is a test oracle only; CI enforces this.
- **Dossier computation is CPU-bound (~1 s)**: run it off the event loop (thread/process pool).
- **Consent is code, not prompt.** Nothing a user types is stored before they tap "I agree";
  consent rows keep the button reply's wamid. Bump `NOTICE_VERSION` when consent copy changes.
- **Personal data is encrypted by the app** (`guruji.crypto`): birth details, the onboarding
  draft and wa_id. The chart dossier holds no name/place but is still personal data.
- **Rule cards**: every factor key must exist in `rag.factors.known_keys()`; cards never use
  fear, lifespan, cure or guarantee language (a test enforces it). Add Hindi/Hinglish
  words to `rag/glossary.py` when retrieval misses; keep `tests/rag` quality test green.
- **Safety replies are scripted, never generated**, and safety detection runs before anything
  else, including before consent (help first; the message text is still not stored). Crisis-
  looking messages skip burst merging and go to the `crisis` queue.
- **Guru side effects go through the turn's `TurnWrite`** (tools buffer facts/readings);
  never write to the store from inside a tool.
- **No PII in logs, traces or alerts.** Use `guruji.logs.user_tag(wa_id)`, never phone numbers,
  names, birth data or message text.
- **Every side effect is idempotent.** Key it by wamid / turn_id / payment id. Workers are at-least-once.
- **Ingress stays thin:** it acks within 50 ms, and anything slow goes on a queue.
- **Credits:** `credit_ledger` is append-only; never charge a user in an escalated/crisis state;
  state the cost before spending a credit; charge only in the turn's `TurnWrite`, and only
  when the answer came through (a failed answer is free, a thumbs-down refunds).
- **Payments:** webhooks never credit anything; they queue a check, and the payment worker
  confirms with Razorpay before `fulfil_order` credits the order, exactly once.
- **Chart facts are checked, not trusted:** keep `agent/verify.py` conservative (a false
  alarm rewrites a good answer); add an eval case whenever a real reply gets a fact wrong.
- **DPDP commands** (STOP, START, delete/export my data) are code, work in every state and
  run right after safety detection. After STOP nothing else gets a reply, including from
  the team. Erasure keeps only payment records and consent proof, on an anonymised row.
- **Guruji never claims to be human**, gives no death/lifespan predictions, medical diagnoses,
  trading calls or legal verdicts, and makes no guaranteed outcomes or fear-based upsells.
- **WhatsApp style:** 1–2 short bubbles per turn, no markdown/lists, mirror the user's language and script.
- **Config:** business knobs (prices, limits, flags) live in the `app_config` table, not in code or env.
  Every key needs a schema and default in `guruji.appconfig`; read it with `ConfigReader`.
- **Admin console:** every view of a user's data is written to `audit_log`; birth details and
  phone numbers stay masked unless an owner reveals them (also audited). Team replies only while
  an escalation is open, only inside WhatsApp's 24-hour window (template after that), and always
  labelled as the team. The console gets no RLS policies; it only talks to the admin API.

## Milestones
M1 foundations (done: pipeline, simulator, schema v1, CI) · M2 astro engine + golden charts (done) ·
M3 guru agent + onboarding (done; persona examples still to curate to 50-100) · M4 RAG rule cards (done; corpus pending astrologer review) · M5 voice + safety/escalation (done) ·
M6 admin console (done; Supabase Auth untested against a real project) · M7 payments, credits,
DPDP, fact-checking, evals, load test, CAPI (done; payments, CAPI and Sail models untested
against the real services) · M8 closed beta
