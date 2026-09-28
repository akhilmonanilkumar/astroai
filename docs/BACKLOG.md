# Backlog: gaps and improvements

What we know is missing, weak or unverified in what has been built so far (M1–M7).
Keep it current: add an item when a gap is found, remove it when it is fixed.
Planned milestone work (M6–M8) is listed at the end only where earlier work depends on it.

## Must fix before any real user

- **Pick the reading model with the eval, on real APIs.** The fact checker
  (`agent/verify.py`) now catches and fixes wrong dasha/transit/placement claims before
  sending, but how often each model gets them wrong first time, and how natural it sounds,
  is unmeasured: run `python -m guruji eval --models sail:<GLM id>,sail:<Kimi id>,
  sarvam:sarvam-105b --judge <model>` (ids from `python -m guruji models sail`) and
  choose. The Sail provider has never been called (the build environment's network
  blocks it). Grow `evals/cases.toml` from real conversations.
- **Payments never ran against Razorpay or WhatsApp.** The order_details card, the
  payment status webhook, the Razorpay receipt lookup (`/v1/orders?receipt=`) and the
  Razorpay webhook are built from the docs and tested only against the simulator. Do a
  live ₹1 test on the real number before the beta; confirm WhatsApp passes our
  `receipt` through to the Razorpay order.
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
- **CI has never run** on GitHub (the repository had no remote until now).
- **Business knobs still in code** that CLAUDE.md says belong in `app_config`: holding-message
  interval (6 h), rule cards per turn, spoken-reply length, place "clear winner" ratio,
  alert repeat interval.
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
- **Most app_config knobs are not enforced yet:** packs, passes, free tier, prashna costs and
  Plus limits wait for M7; `busy_mode` and `new_user_admission` for the viral-spike playbook.
  Only `flags.voice_enabled` and `human_template` are live.

- **Not built from M7's list:** UPI Autopay mandates (prepaid passes only), GST invoice
  PDFs after purchase, the one-time ₹11 trial offer near the end of the 72-hour window
  (it shows only when the user asks or runs out), busy mode and admission control.
- **Load tested only in one dev process** (`python -m guruji loadtest`): 120 messages, p50
  2.9 s to the first bubble, almost all of it the 2.5 s burst-merge window. Repeat on the
  droplet with real Redis/Postgres and a real LLM.
- **Conversions API** events (Lead, Purchase) are built from Meta's docs and untested.

## Planned, and needed by the above

- M8 closed beta: daily persona tuning from real transcripts, and the live checks above.
