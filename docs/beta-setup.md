# Beta setup: demo now, production after approval

The company isn't registered yet, so Meta Business verification and Razorpay KYC can't
start. Everything else can run today on **demo accounts**: Meta's WhatsApp test number,
Razorpay test mode and the real Supabase project. Moving to production later is a set of
config changes plus the approvals, with no code changes. This page covers both.

| Piece | Demo (now) | Production (after approval) |
| --- | --- | --- |
| WhatsApp | Meta developer app, free test number, up to 5 recipient numbers you add | The company's own number on a verified business; display name approved |
| Payments | Razorpay test mode, `PAYMENT_CHECKOUT=link` (a "Pay ₹N" link button) | Razorpay live keys; `PAYMENT_CHECKOUT=whatsapp` once Razorpay is linked in WhatsApp Manager (or keep `link`) |
| Database | Supabase, Mumbai (already migrated) | Same project, Pro plan |
| Server | The droplet with `docker-compose.prod.yml` (or a tunnel from a laptop) | Same droplet |
| `ENV` | `staging` | `prod` |

`ENV=staging` already refuses everything that belongs to dev: simulator addresses, dev
secrets and keys, `memory://` stores, fake models and dev sign-in. A mistake shows up at
startup, before any user sees it.

## 1. Server and HTTPS

Meta and Razorpay only call HTTPS URLs, so the stack needs a public address.

**Droplet (recommended).** A DigitalOcean droplet in BLR1 with Docker, and two DNS
records pointing at it (for example `api.<domain>` and `admin.<domain>`):

```
cp backend/.env.example backend/.env.prod    # fill in (section 6)
cp admin/.env.example admin/.env.prod        # SUPABASE_URL, SUPABASE_ANON_KEY
INGRESS_DOMAIN=api.<domain> ADMIN_DOMAIN=admin.<domain> \
  docker compose -f docker-compose.prod.yml up -d --build
```

Caddy gets the certificates. Only the webhooks (`/webhook`, `/razorpay/webhook`,
`/telegram/webhook`) and the console are public. Every deploy runs `migrate` first, and
the roles wait for it.

**Laptop, for a quick try.** Run Redis in Docker and the roles locally with the same
`.env.prod` values, then expose the ingress with a tunnel:
`cloudflared tunnel --url http://localhost:8000`. The tunnel URL changes on every run,
so the webhooks have to be updated each time.

## 2. Supabase

The schema is applied (`python -m guruji migrate`, which also runs on every deploy).

- `DATABASE_URL`: use the **transaction pooler** URI (port 6543), not the direct
  `db.<ref>.supabase.co:5432` host. The code is built for the pooler, and the direct
  host is IPv6-only.
- Set **real encryption keys now**, before the first real user. Everything personal is
  encrypted with them, and they can never change afterwards without losing that data.
  Generate each one with the command below and keep both in a password manager:
  `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`
  → `FIELD_ENCRYPTION_KEY`, `LOOKUP_HMAC_KEY`.
- Auth for the console: sign-ups off, TOTP on, then create each team member under Auth →
  Users and run `docker compose -f docker-compose.prod.yml run --rm admin-api add-admin
  <email> --role owner`.

## 3. WhatsApp: Meta's test number

1. On developers.facebook.com, create an app of type **Business** and add the WhatsApp
   product. Meta provides a test phone number and a test WhatsApp Business Account.
2. Under **API Setup**, add up to 5 recipient numbers (yours and the testers'). Each one
   confirms with a code. The test number can only message these.
3. The access token shown there expires in 24 hours. For a stable demo, create a
   **System User** in Business Settings, give it the app and the WhatsApp account, and
   generate a token with `whatsapp_business_messaging` and
   `whatsapp_business_management`.
4. Under **Configuration**, set the webhook to `https://api.<domain>/webhook` with a
   verify token you choose, and subscribe to the `messages` field.
5. Set these in `backend/.env.prod`: `WA_PHONE_NUMBER_ID` (the test number's id),
   `WA_ACCESS_TOKEN` (the system user token), `WA_APP_SECRET` (App settings → Basic),
   `WA_VERIFY_TOKEN`, and `GRAPH_API_BASE=https://graph.facebook.com`.

The test number can't take WhatsApp-native payments, which is why the demo uses payment
links. For 50–100 beta users, 5 recipients isn't enough: you'll need your own number.
Meta lets an unverified business register one with lower messaging limits. Check the
current limits in WhatsApp Manager, since Meta changes them.

## 4. Razorpay test mode

1. Sign up at dashboard.razorpay.com. Test mode works right away, without KYC.
2. In test mode, go to Settings → API Keys and generate a key pair:
   `RAZORPAY_KEY_ID=rzp_test_...` and `RAZORPAY_KEY_SECRET`.
3. Settings → Webhooks: add `https://api.<domain>/razorpay/webhook` with a secret
   (`RAZORPAY_WEBHOOK_SECRET`) and the events `payment_link.paid`, `payment.captured` and
   `order.paid`. Without a webhook, payments are still found by polling for about 20
   minutes; the webhook only makes it instant.
4. Set `PAYMENT_CHECKOUT=link` and `RAZORPAY_API_BASE=https://api.razorpay.com`.

**The ₹1 test.** In the console, go to Config → `packs` and add
`{"id": "test1", "price_inr": 1, "prashnas": 1}`. On WhatsApp, send "recharge", pick ₹1
and pay on the Razorpay page with a test method from Razorpay's test-mode docs (for UPI,
`success@razorpay`). The credit should arrive with a confirmation message. Remove the
pack afterwards.

## 5. Alerts and the closed beta

- **Telegram** (required outside dev): create a bot with BotFather, then set
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_WEBHOOK_SECRET`. Register
  `https://api.<domain>/telegram/webhook` with that secret so the Ack buttons work.
- **Invite codes**: in Config → `beta`, set
  `{"invite_only": true, "codes": ["GURU-BETA-1"]}`. Share a link that pre-fills the
  code: `https://wa.me/<number>?text=GURU-BETA-1`. People without a code get a polite
  waitlist reply. Use one code per cohort or channel, so you can see where people came
  from (`users.invite_code`) and retire a code by removing it.

## 6. `backend/.env.prod` checklist

`ENV=staging`, `DATABASE_URL` (pooler), `FIELD_ENCRYPTION_KEY`, `LOOKUP_HMAC_KEY`,
`SARVAM_API_KEY`, the `WA_*` values and `GRAPH_API_BASE`, the `RAZORPAY_*` values and
`PAYMENT_CHECKOUT`, the `TELEGRAM_*` values, `ADMIN_AUTH=supabase`, `SUPABASE_URL`,
`ADMIN_CONSOLE_URL=https://admin.<domain>`, `PRIVACY_NOTICE_URL` (the live landing page;
bump `NOTICE_VERSION` whenever its text changes). `REDIS_URL` and `BIND_HOST` come from
the compose file.

Smoke test after each deploy: send "namaste" from a recipient number and finish
onboarding, ask a question, do the ₹1 payment, send "balance", react 👎 to an answer (it
should appear under Console → Feedback), and send "STOP" then "START".

## 7. Moving to production

Once the company is incorporated:

1. **Meta Business verification** with the company documents and live website. Then add
   the company's number to the WhatsApp account, get the display name approved, and move
   the webhook to it. Change `WA_PHONE_NUMBER_ID` and `WA_ACCESS_TOKEN` (a new system user
   token if the account changed).
2. **Templates**: get `team_followup` approved in English and Hindi (see `BACKLOG.md`).
3. **Razorpay KYC**, then switch to live keys (`rzp_live_...`), a live-mode webhook and a
   new `RAZORPAY_WEBHOOK_SECRET`.
4. **Native checkout (optional)**: link Razorpay as a payment configuration in WhatsApp
   Manager, set `WA_PAYMENT_CONFIG` to its name and `PAYMENT_CHECKOUT=whatsapp`. Payment
   links keep working if you'd rather not switch; both are checked the same way.
5. `ENV=prod`. Keep the same Supabase project and the **same encryption keys**.
6. Remove the ₹1 test pack; set `beta.invite_only` to false when the beta opens up.

## Daily persona tuning (the M8 loop)

Every day during the beta:

1. **Console → Feedback, Disliked.** Read each answer next to the message it replied
   to. When the reason isn't clear, open the user's conversation; each view is audited.
2. Fix the cause in the right place:
   - tone and style: `persona/guruji.md`, with a new example exchange;
   - a wrong chart fact: a case in `evals/cases.toml`, and a `verify.py` rule if it's
     missing;
   - missing astrology: a rule card.
3. Run `python -m guruji eval --models sarvam:sarvam-105b --judge sarvam:sarvam-105b`
   before deploying, and compare the result with the previous report.
