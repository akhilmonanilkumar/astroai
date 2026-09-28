# Backlog: gaps and improvements

What we know is missing, weak or unverified in what has been built so far (M1–M8).
Keep it current: add an item when a gap is found, remove it when it is fixed.
Planned milestone work (M6–M8) is listed at the end only where earlier work depends on it.

## Must fix before any real user

- **Grow the eval from real conversations.** The 2026-09-28 bake-off (20 cases) kept
  sarvam-105b as the reading model: 90% of final replies factually right, 4.4/5 natural,
  1.6 s median, as good as GLM-5.3 and Kimi-K3 and much faster; we use Sarvam only.
  Twenty cases is small: add real conversations to `evals/cases.toml`, and check
  `en-late-night-anxiety`, which failed the language check for two models.
- **Payments never ran against Razorpay or WhatsApp.** Both checkouts (WhatsApp's
  order_details card and Razorpay payment links) and both webhooks are built from the docs
  and tested against the simulator only. Do the ₹1 test in Razorpay test mode with
  payment links (`docs/beta-setup.md` §4). Still unconfirmed: that
  `GET /v1/payment_links?reference_id=` filters (the code doesn't rely on it; it matches
  on its side), and, for native checkout later, that WhatsApp passes our `receipt` through.
- **Money, privacy and payment copy needs review** (`agent/credits_copy.py`,
  `agent/privacy.py`, `billing/catalog.py`), especially Hindi and Hinglish, and the
  deletion wording against the landing page's promises.
- **The team follow-up template must be approved by Meta.** After 24 hours the console can
  only send the utility template named in app_config `human_template` (`team_followup` by
  default, no parameters). Create and get it approved in WhatsApp Manager, in English and
  Hindi, before the beta.
- **Safety copy needs expert review.** The scripted crisis, medical, abuse and legal replies
  (`safety/messages.py`) in English, Hinglish and Hindi should be reviewed by a mental-health
  professional; helpline numbers (Tele-MANAS 14416, 112, 108, 181) re-verified.
- **Privacy notice URL is a placeholder** (`PRIVACY_NOTICE_URL`), and the landing page's
  legal pages, retention periods (`app_config.retention`) and `docs/breach-runbook.md`
  need a lawyer's review.

## Accuracy and quality

- **Crisis detection before onboarding completes relies on rules only.** The model's second
  opinion runs on active users' turns (it shares the talk/reading routing call); users still
  onboarding are covered only by the keyword rules in `safety/detect.py`.
- **Dissatisfaction and payment-dispute escalations** (categories in the schema) are never
  raised automatically. A thumbs-down refunds the credit but doesn't escalate.
- **Follow-ups are free by time, not topic:** any reading question within
  `prashna.followup_hours` of a paid one, up to `followups`, is free even if the topic
  changed. Generous on purpose; revisit with real usage.
- **Fact checker coverage:** it judges dashas, dates, natal house/sign, lagna, rashi,
  transits and Sade Sati. Yogas, nakshatras, dignities, aspects and navamsa claims are not
  checked yet; sentences with several grahas sharing one claim are checked per graha.
- **Readings ledger depends on the model calling `record_reading`.** Nothing enforces it, so
  some readings may go unrecorded and consistency checks will miss them.
- **Conversation memory is the last 12 messages** plus facts and readings; no rolling
  summary of older conversation.
- **Language detection is a word-list heuristic** (en / Hinglish / Hindi). Other Indian
  languages are not supported: speech-to-text detects them, but replies and TTS fall back to
  Hindi.
- **Onboarding extraction without the LLM** handles common formats only; unusual answers need
  the fast model.
- **Voice replies are capped at ~30 s (450 characters)** and cut at a sentence boundary, so a
  long answer can be spoken only partly while the full text is stored.
- **Sarvam reading model runs with thinking off.** At "low" reasoning it thought for 30+ s per
  reply. Revisit when latency allows; compare answer accuracy with thinking on.
- **Routing adds a model call (~0.3 s)** on every non-trivial turn.

## Content that needs a domain expert

- **Rule-card corpus (208 cards) is unreviewed.** Written as original summaries of Parashari
  principles; needs a practising jyotishi's review before beta.
- **Missing cards:** house-lordship combinations ("lord of the 7th in the 10th", 144 cards),
  Rahu transits outside the 3rd/6th/11th, divisional charts beyond D9.
- **Persona bible has ~9 example exchanges;** the plan calls for 50–100, curated from beta
  transcripts.
- **Onboarding copy in Hindi and Hinglish** should get a native-speaker review.
- **Dignity convention:** exaltation is whole-sign (Moon anywhere in Taurus, Mercury anywhere
  in Virgo), matching popular software; confirm with the reviewing astrologer.

## Engineering

- **Not tested against the real services:** WhatsApp Cloud API (media upload, `voice: true`
  audio messages, button replies, templates), Telegram Bot API, Supabase (pooler in
  transaction mode, RLS, Auth: JWKS token check, TOTP enrollment in the console). Only the simulator, mocks and plain Postgres + pgvector have been used.
- **CI failed on the M7 pull request** (backend tests; the log needs a GitHub login). The
  tip of `dev` passes the same steps in a Linux container, and CI now also runs on pushes
  to `dev`: check the first run.
- **Business knobs still in code** that CLAUDE.md says belong in `app_config`: holding-message
  interval (6 h), rule cards per turn, spoken-reply length, place "clear winner" ratio,
  alert repeat interval. The full list is in "Hard-coded constants" under the
  production-readiness audit below.
- **No rate limiting, busy mode or LLM overflow handling** (the plan's viral-spike playbook).
- **No observability:** Sentry and trace tooling (with PII scrubbing) not integrated.
- **Dossier computation is ~1 s of CPU** in a thread; move to a process pool if it contends
  under load.
- **Place index takes ~5 s and a few hundred MB to load** in each turn worker.
- **No size limit on downloaded voice notes** before transcription.
- **Simulator voice notes from the browser are WebM,** which is not split for the 30-second
  speech-to-text limit (WhatsApp's OGG notes are).
- **Two different user tags** in logs: `user_tag(wa_id)` in the turn worker and a tag from the
  internal user id in alerts. Escalation IDs are logged in both, to correlate.
- **Ephemeris range is 1850–2149** and latitudes beyond ±66° get no lagna; no birth-time
  rectification for unknown times.
- **Intermediate commits are not individually buildable:** the history was committed
  feature by feature after the work was done, so some early commits reference modules added
  by later ones. The tip of `dev` builds and passes.

- **Admin console gaps:** no live push (the inbox polls every 10 s, a thread every 5 s); no
  per-case assignment between team members; no way to disable a team member other than SQL
  (`admins.disabled_at`); audit views are deduplicated per person for 30 minutes through
  Redis; metrics run live SQL (fine for the beta, will need rollups at scale).
- **`flags.busy_mode` is not enforced** (the viral-spike playbook). Every other app_config
  knob is live, including `flags.new_user_admission` and `beta` (invite codes).
- **Feedback on a paid answer shows the confirmation tap as the question.** When an answer
  came after "Haan, dekhiye" (confirming a credit), the rated turn's message is that tap;
  the real question is one turn earlier in the conversation view.
- **The simulator can't send reactions**, so 👎/👍 feedback and refunds are exercised only by
  tests, not by hand in dev.
- **A failed attempt on a payment link isn't told to the user** (the link stays payable and
  the order pending until it expires); WhatsApp-native checkout does tell them.
- **No user-count cap for the beta:** invite codes gate entry, but nothing stops at N users;
  remove a code to close it.

- **Not built from M7's list:** UPI Autopay mandates (prepaid passes only), GST invoice
  PDFs after purchase, the one-time ₹11 trial offer near the end of the 72-hour window
  (it shows only when the user asks or runs out), busy mode and admission control.
- **Load tested only in one dev process** (`python -m guruji loadtest`): 120 messages, p50
  2.9 s to the first bubble, almost all of it the 2.5 s burst-merge window. Repeat on the
  droplet with real Redis/Postgres and a real LLM.
- **Conversions API** events (Lead, Purchase) are built from Meta's docs and untested.

## Planned, and needed by the above

- M8 closed beta: the code is in (invite codes, payment links, feedback view, `migrate`,
  `docker-compose.prod.yml`). What's left is running it: the demo accounts and deploy in
  `docs/beta-setup.md`, the live checks above, and daily persona tuning from the Feedback
  view.

## Production-readiness audit (2026-09-28)

A line-by-line pass over `backend/src`, the migrations, the admin console, the landing
site, the Dockerfiles, compose, Caddy and CI. Items already listed above are not
repeated. IDs are stable so they can be referenced from commits and PRs.

P0 = a real user could lose money or data, or miss help, so fix before the beta opens.
P1 = needed to run production reliably and securely. P2 = hygiene and scale.

### P0: money, data and safety bugs

All ten fixed on 2026-09-28 (PR-01 to PR-10, plus PR-51). What they left open:

- **Refund and dispute handling is built from Razorpay's docs** and tested with fakes
  only: do a test-mode refund and a test dispute alongside the ₹1 test, and confirm the
  `refund.processed` and `payment.dispute.*` payloads and `GET /v1/disputes/:id`.
- **The WhatsApp checkout card's `expiration`** is from Meta's docs; confirm the card
  shows as expired after `pending_order_hours`.
- **Before consent, voice notes now go to Sarvam** for crisis detection only (PR-06). Add
  this to the privacy notice (a lawyer's call) and to Sarvam's DPA check (PR-36).
- **The turn lock isn't renewed** during a turn (PR-05 bounds the guru at 40 s instead);
  keep `turn_lock_seconds` above transcription + `guru_budget_seconds`.
- **Refund, payment-link and dispute copy** in Hindi and Hinglish (`billing/catalog.py`)
  needs the same native-speaker review as the rest of the money copy.

### P1: reliability and operations

- **PR-11 No service metrics or ops alerting.** There is nothing on queue depth, turn
  latency (p50/p95 to the first bubble), LLM latency, errors and tokens, payment outcomes or
  Meta send failures, and no alert except escalations. Add Prometheus metrics (or push to
  Grafana Cloud or Datadog) plus alerts on queue depth, dead letters, error rate and
  latency. The "No observability" item above covers Sentry and traces only.
- **PR-12 No dead-letter tooling.** There is no command to list, replay or purge `q:dead`,
  and nobody is told when a job lands there. Add `python -m guruji dead list|replay|purge`
  and an alert.
- **PR-13 A hung worker looks healthy.** The turn, sender, jobs, coalescer and alerts roles
  expose no health check or heartbeat, and compose has no `healthcheck` for app containers.
  The `/healthz` endpoints ping Redis only, not Postgres. Add heartbeats in Redis and
  compose health checks.
- **PR-14 One droplet is a single point of failure.** Redis holds the queues, locks and
  dedupe keys with AOF on a local volume: no backups, no replica, no written RPO/RTO. Move
  to managed Valkey/Redis or add snapshots, and set `maxmemory-policy noeviction` (queues
  must never be evicted).
- **PR-15 No deploy pipeline.** Images are built on the production box
  (`docker compose up -d --build`); there is no registry, no image tags, no rollback and no
  rolling restart. The plan calls for GitHub Actions → DO Container Registry → a rolling
  restart per role.
- **PR-16 Staging and production share one Supabase project** (`docs/beta-setup.md` §7).
  Demo data mixes with real users, and migrations are first tried on production. Use two
  projects with separate encryption keys.
- **PR-17 Builds aren't reproducible.** GeoNames is downloaded unpinned with no checksum;
  the embedding model isn't pinned to a revision; `python:3.12-slim`, `node:22-slim`,
  `caddy:2`, `redis:7-alpine` and `uv:0.12` are pinned by tag, not digest; GitHub Actions are
  pinned by tag, not SHA.
- **PR-18 Compose sets no resource limits.** Each turn worker holds the place index (a few
  hundred MB) and the embedding model; one OOM can take down the whole box, Redis included.
  Set memory and CPU limits per service.
- **PR-19 The DB pool is small and turns make many round trips.** The pool max is 5
  (`config.py:89`) against 50 concurrent turns (`worker_concurrency`). `_guru` makes about
  10 sequential store reads (balance, pass, facts, readings, history, config), and
  `commit_turn` sends one statement per row. Fix: `asyncio.gather` the reads, batch the
  commit with `executemany` or a pipeline, and size the pool to Supavisor's limit.
- **PR-20 CPU work runs on the event loop.** The `transits_on` tool, the fact check
  (`check_reply` inside the `review` middleware) and fuzzy place search
  (`geo/places.py:76`) run in the loop; `_CHART_SLOTS = 2` is a module constant
  (`agent/graph.py:85`). Use `to_thread` or the process pool the plan describes.
- **PR-21 No global rate limits for Sarvam or Meta** (planned: Redis limiters, then
  overflow to the fallback model, then busy mode). Speech-to-text chunks run in an unbounded
  `gather` (`voice/speech.py`), so a 10-minute note fires about 22 parallel requests.
- **PR-22 WhatsApp errors are classed by HTTP status only** (`whatsapp/client.py`
  `_raise_for`). Meta's error codes decide what to do: 131047 (24-hour window closed),
  131026 (undeliverable), 131056 (pair rate limit: retry later), 130429 (throughput). Today
  every 4xx is dead-lettered, and rate limits aren't retried.
- **PR-23 Delivery statuses are ignored.** Webhook `statuses` (sent/delivered/read/failed)
  are dropped, so failed deliveries go unseen and Meta's per-message charges (from
  2026-10-01) can't be reconciled. Log them and count failures.
- **PR-24 The native checkout card is never updated.** No `order_status` message is sent
  after payment, so the "Review and pay" card stays pending in the chat. The order also
  carries no tax breakdown (GST).
- **PR-25 The retention sweep runs unbounded statements** (`db/postgres.py:254`): one
  `delete from messages where created_at < …` and one erase per user in a loop. Batch it
  (N rows per run) so it never holds long locks through the pooler.
- **PR-26 Every turn worker syncs rule cards at startup** (`__main__.py` `_retriever` →
  `sync_cards`), embedding on the event loop and slowing boot. Move it to the
  `migrate`/deploy step.
- **PR-27 Crisis alerts go only to Telegram.** If Telegram or the bot is down, severity-1
  alerts are lost after retries. Add a second channel for severity 1 (a phone call or SMS
  via PagerDuty or Exotel), an on-call rota, and an alert on dead-lettered alert jobs.
- **PR-28 The Redis client has no timeouts** (`redis_conn.py`): `Redis.from_url` runs with
  no socket timeout, health-check interval or retry policy. A network stall hangs workers.
- **PR-29 Ingress hardening.** No request-size limit (Caddy or FastAPI); no per-IP rate
  limit on the public webhooks; `/telegram/webhook` returns 500 on a body that isn't JSON
  (`ingress/app.py:112`); `/healthz` is public on the API domain; uvicorn runs behind Caddy
  without `proxy_headers`/`forwarded_allow_ips`.
- **PR-30 Logs are plain text and stay on the box.** There is no JSON format, no
  `turn_id`/request id on every line, and no shipping. The json-file driver keeps 100 MB
  per service, only days at beta volume.

### P1: security

- **PR-31 Secrets sit in `backend/.env.prod` on the droplet** (the plan says a vault), and
  there is no rotation runbook for the WhatsApp token, Razorpay keys, Telegram, Sarvam or
  Supabase. Encryption keys can't be rotated: blobs carry a version byte, but only
  `_VERSION = 1` decrypts (`crypto.py`). Add a key ring (decrypt old, encrypt new) and a
  re-encrypt job.
- **PR-32 `wa_access_token`, `wa_app_secret` and `wa_verify_token` are plain `str`**
  (`config.py`), not `SecretStr`, so they can leak through reprs and error messages.
- **PR-33 The admin console has no Content-Security-Policy.** supabase-js keeps the
  session in localStorage, so any XSS means account takeover. Add a CSP and a
  Permissions-Policy, and consider Cloudflare Access or an IP allowlist in front of the
  admin domain.
- **PR-34 Admin API guard rails.** No rate limiting. No reason is captured when an owner
  reveals personal data. No step-up MFA for reveal, erase or credit adjustments. Credit
  adjustments go up to ±1000 per call with no daily cap and no second approval.
- **PR-35 CI has no security scanning.** No pip-audit or npm audit, no secret scanning
  (gitleaks), no SAST (bandit/semgrep), no image scan (trivy), no Dependabot or Renovate.
- **PR-36 LLM data minimisation.** The user's name, full chart and remembered facts go to
  Sarvam on every turn. Confirm Sarvam's DPA, retention and India residency, and leave the
  name out unless the reply needs it. User text can steer `remember_fact` into storing
  arbitrary text: low impact, but cap and clean it.

### P1: product gaps against the plan (not listed above)

- **PR-37 No proactive check-in within the 72-hour CTWA window** (free business-initiated
  message).
- **PR-38 No Guru Plus expiry reminders** ("Guruji reminds before expiry").
- **PR-39 Credits never expire.** The ledger reason `expire` exists but no job uses it, and
  the landing page publishes no expiry policy (the plan requires one).
- **PR-40 Safety rules cover English, Hinglish and Hindi only.** Speech-to-text accepts
  other Indian languages, so a crisis note in Tamil, Telugu, Bengali or Marathi relies on the
  model's second opinion, which only active users get. Add crisis keywords for the main
  regional languages.
- **PR-41 The "spend a credit?" question never expires** (`agent/graph.py:620`): tapping
  "Haan" days later answers a stale question. Expire `meter.pending` after a configurable
  time.
- **PR-42 The blind "real astrologer?" evals aren't in CI** (plan). Run a nightly eval
  with the real key and fail on regressions against the last report.
- **PR-43 Landing site placeholders** (`landing/site.ts`): brand, domain `example.in`,
  WhatsApp number `910000000000`, legal name, CIN, GSTIN, address, support and grievance
  emails and the grievance officer's name are all TODO. Meta verification, Razorpay KYC and
  DPDP all need them. The landing site isn't built in CI either.
- **PR-44 Data export limits.** It is cut at 5,000 messages without telling the user
  (`jobs.py:30`), and the file (phone number and birth details in clear) passes through
  Meta's media servers. Say when it's truncated, and consider a short-lived signed link.

### P2: code hygiene, tests and scale

- **PR-45 `_cost_reply` has two docstrings**; the second is a dead string
  (`agent/graph.py:714`).
- **PR-46 The Guru Plus end date in the payment confirmation is in UTC**
  (`billing/worker.py:101`) while the balance reply uses IST, so it can be off by a day
  near midnight.
- **PR-47 `Settings.max_bubbles_per_turn` is never read**; `agent/guru.py` hard-codes
  `MAX_BUBBLES = 2`. `voice_enabled` is both an env setting and an app_config flag. Keep one
  of each.
- **PR-48 IST is defined in four modules** (`ZoneInfo("Asia/Kolkata")` in graph, guru,
  metering and billing). Keep one in a shared module.
- **PR-49 `Worker` reads the private `asyncio.Semaphore._value`** (`queue/streams.py:218`).
- **PR-50 Some HTTP clients are never closed:** TelegramAlerter, SarvamSpeech (two
  instances) and MetaCapi.
- **PR-52 Migrations apply out of order silently** (`db/migrate.py:22`). Files are dated
  up to 2026-10-10, so one created today would run last on production but in the middle on
  a fresh database. Refuse a version older than the newest applied one, and take an
  advisory lock while migrating.
- **PR-53 Index gaps.** Nothing indexes `users.opted_out_at` (the retention scan).
  `recent_messages`, `facts` and `readings` order by `id`, but their indexes are on
  `created_at`. The balance is a `SUM` over the ledger on every call, and once per row in
  the admin user list; keep a balance column updated with the ledger at scale.
- **PR-54 Test gaps.** No coverage threshold in pytest. The admin console has no lint
  (ESLint) and no tests (Playwright).
- **PR-55 Minor.** `MIN_YEAR = 1900` for birth dates (`agent/extract.py:23`) against an
  ephemeris from 1850; the one TTS voice (`tts_speaker=aditya`) is used for every user.

### Hard-coded constants

Every tunable found in code. "Config" means an `app_config` key (a business knob, per
CLAUDE.md); "Settings" means an env setting (ops tuning). Protocol limits (WhatsApp's
3 buttons, 24-row titles, the 24-hour window, Sarvam's 30 s STT cap) and astronomy
constants are rightly code and aren't listed.

| Where | Constant | Value | Move to |
| --- | --- | --- | --- |
| `agent/graph.py:103` | `HOLDING_EVERY` (holding reminder) | 6 h | Config |
| `agent/graph.py:85` | `_CHART_SLOTS` (charts cast at once) | 2 | Settings |
| `agent/graph.py:666-667` | facts / readings in the prompt | 30 / 15 | Config |
| `agent/graph.py:537` | messages scanned for the last holding reply | 20 | Settings |
| `agent/guru.py:45,236` | `MAX_BUBBLES`, `MAX_REPLY_CHARS` | 2, 900 | Config |
| `agent/guru.py:50,404` | `MAX_FACT_REWRITES`, model-call `run_limit` | 2, 8 | Settings |
| `agent/guru.py:46` | `_MAX_TOOL_PERIODS` | 40 | Settings |
| `agent/llm.py:31-34` | max tokens: reading / talk / thinking | 1500 / 1000 / 12000 | Settings |
| `agent/router.py:43` | `_MAX_SMALL_TALK_WORDS` | 5 | Settings |
| `agent/commands.py:12`, `agent/privacy.py:19` | `_MAX_WORDS` for commands | 6, 8 | keep; test-covered |
| `agent/onboarding.py:26-27` | place options, `_CLEAR_WINNER_RATIO` | 2, 20 | Config (ratio) |
| `agent/extract.py:23` | `MIN_YEAR` | 1900 | align with the ephemeris |
| `agent/language.py:27,43` | language-switch word counts | 2, 8 | Settings |
| `voice/speech.py:22-27,125` | STT/TTS models, `MAX_SPOKEN_CHARS`, `MAX_TTS_CHARS`, sample rate | saaras:v3, bulbul:v3, 450, 2400, 24000 | Settings (models), Config (spoken length) |
| `voice/speech.py:81` | Sarvam HTTP timeout | 60 s | Settings |
| `rag/retrieve.py:16-22` | `CHART_CARDS`, `GENERAL_CARDS`, `GENERAL_MIN_SIMILARITY`, `CORE_CARDS` | 5, 2, 0.35, 6 | Config |
| `rag/index.py:19,27` | `RRF_K`, `_STEM` | 60, 5 | keep; quality test |
| `geo/places.py:30-32` | `_ALT_NAME_MIN_POP`, `_FUZZY_CUTOFF` | 15000, 85 | keep; quality test |
| `queue/streams.py:99-100` | `block_ms`, `claim_idle_ms` | 1 s, 60 s | Settings (claim > turn budget) |
| `queue/streams.py:86` | retry backoff cap | 30 s | Settings |
| `queue/streams.py` (`_dead`) | dead-letter `maxlen` | 10000 | keep; trimmed by age too |
| `queue/streams.py:64` | enqueue dedupe TTL | 24 h | Settings |
| `ingress/coalescer.py:32,111` | `LOCK_RETRY_SECONDS`, flush batch | 0.5 s, 500 | Settings |
| `turn/worker.py`, `sender/worker.py` | `RetryJob` delays on a held lock | 1.0 s, 0.5 s | Settings |
| `__main__.py:228,255` | alerts / jobs worker concurrency | 4, 8 | Settings |
| `jobs.py:198` | retention sweep interval | 3600 s | Settings |
| `jobs.py:30` | `_EXPORT_MESSAGES`, export page size | 5000, 500 | Config |
| `billing/worker.py:83-87` | payment re-check backoff, counter TTL | 10 s × 2ⁿ up to 600 s, 3 days | Settings |
| `ingress/app.py:77` | payment-check dedupe | 60 s | Settings |
| `turn/worker.py` | CAPI Lead dedupe | 30 days | keep |
| `whatsapp/client.py`, `billing/razorpay.py`, `alerts/worker.py`, `jobs.py` | HTTP timeouts | 15 s | Settings |
| `jobs.py` (`MetaCapi`) | `https://graph.facebook.com` | hard-coded | reuse `GRAPH_API_BASE` |
| `admin/app.py:42-43` | `AUDIT_DEDUPE_SECONDS`, `MAX_REPLY_CHARS` | 1800, 3000 | Settings |
| `admin/app.py` (`CreditsIn`) | credit adjustment bound | ±1000 | Config |
| `safety/detect.py` | `SEVERITY` per category (re-ping only at 1) | 1/1/1/2/3 | keep; review with the safety copy |
| `appconfig.py` (`ConfigReader`) | config cache TTL | 30 s | Settings |
| `config.py:89` | `db_pool_max` against `worker_concurrency` | 5 against 50 | see PR-19 |
