# Backlog: gaps and improvements

What we know is missing, weak or unverified in what has been built so far (M1–M5).
Keep it current: add an item when a gap is found, remove it when it is fixed.
Planned milestone work (M6–M8) is listed at the end only where earlier work depends on it.

## Must fix before any real user

- **"STOP" and "delete my data" are promised but not implemented.** The consent message
  tells users they can send either at any time. Nothing handles them yet (planned for M7,
  DPDP). Either build them before the beta or change the consent copy and bump
  `NOTICE_VERSION`.
- **The guru sometimes gets dasha timing wrong.** Live tests on Sarvam showed it confusing
  the running antardasha with the next one (e.g. saying Jupiter antardasha starts next
  April when Jupiter is running now and Saturn starts next April), even though the correct
  dates are in its prompt. Needed: an automatic check of dasha/transit/date claims in each
  reply against the dossier before sending, and a regression eval set of real questions.
- **The team cannot reply to escalated users.** Escalations alert the team and stop the
  guru, but there is no way to answer the user yet (admin console, M6). Closing a case is
  a CLI command (`python -m guruji resolve-escalation`). Human replies after 24 hours also
  need an approved WhatsApp utility template.
- **Safety copy needs expert review.** The scripted crisis, medical, abuse and legal replies
  (`safety/messages.py`) in English, Hinglish and Hindi should be reviewed by a mental-health
  professional; helpline numbers (Tele-MANAS 14416, 112, 108, 181) re-verified.
- **Privacy notice URL is a placeholder** (`PRIVACY_NOTICE_URL`), and the landing page's
  legal pages need a lawyer's review.

## Accuracy and quality

- **Crisis detection before onboarding completes relies on rules only.** The model's second
  opinion runs on active users' turns (it shares the talk/reading routing call); users still
  onboarding are covered only by the keyword rules in `safety/detect.py`.
- **Dissatisfaction and payment-dispute escalations** (categories in the schema) are never
  raised automatically.
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
  audio messages, button replies), Telegram Bot API, Supabase (pooler in transaction mode,
  RLS). Only the simulator, mocks and plain Postgres + pgvector have been used.
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

## Planned, and needed by the above

- M6 admin console: inbox and human replies for escalations, users, metrics, controls,
  `app_config` editing.
- M7 payments and credits (cost stated before a credit is spent, never charged while
  escalated), DPDP export/delete, retention, breach runbook, evals in CI, load test,
  Conversions API.
