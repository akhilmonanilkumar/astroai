# Personal data breach runbook

What to do when personal data may have been exposed, lost or changed without
authorisation. Written for the DPDP Act 2023 and the DPDP Rules 2025; have a lawyer
confirm the deadlines and wording before launch.

## What counts

Any unauthorised access, disclosure, loss or change of personal data we hold, for
example: a leaked database URL or service-role key, a leaked `FIELD_ENCRYPTION_KEY` or
`LOOKUP_HMAC_KEY`, a lost laptop with a database dump, a bug that sent one user's
reading to another, an admin account used by someone else, or a vendor (Supabase,
DigitalOcean, Sarvam, Meta, Razorpay, the LLM provider) reporting an incident.

Personal data here: phone numbers, names, birth details, charts, conversations, voice
notes, life facts, payments. Birth details and phone numbers are encrypted by the app
(`guruji.crypto`); conversations are not.

## First hour: contain

1. Open an incident note (date, who, what is known). Keep adding to it; it becomes the
   record the Board and users may ask for. No personal data in the note itself.
2. Stop the leak: rotate the exposed secret (database password, Supabase service key,
   WhatsApp token, Razorpay keys, Telegram token), revoke the admin's Supabase session and
   set `admins.disabled_at`, or take the affected role down.
3. If an encryption key leaked: generate a new key, deploy it, and re-encrypt stored
   fields before discarding the old key. Treat all data encrypted under the old key as
   exposed if the database may also have been read.
4. Preserve evidence: keep logs, the audit log (`audit_log`), Supabase and DigitalOcean
   access logs. Don't delete anything that shows what happened.

## Within 24 hours: assess

- Which data, how many users, since when, and whether it was encrypted.
- The admin console's audit log shows who viewed or revealed which user's data.
- Decide whether it is a personal data breach under DPDP. When unsure, treat it as one.

## Notify

- **Data Protection Board of India:** an intimation without delay once known, then a
  detailed report within 72 hours (nature, extent, timing, likely impact, what was done,
  what users should do). Use the Board's online form.
- **Affected users:** without delay, in plain language, in their language (en / Hindi /
  Hinglish), on WhatsApp: what happened, what data, the likely consequences, what we are
  doing, what they can do, and the grievance contact. Use an approved utility template
  when outside the 24-hour window. The message text is reviewed before sending; never
  generated.
- **CERT-In:** cyber incidents within 6 hours of noticing (CERT-In directions, 2022).
- **Partners** whose terms require it (Meta, Razorpay).

## Afterwards

- Write up the cause and the fix; add a test or check that would have caught it.
- Record the incident and the notifications; keep the record at least as long as the
  retention policy requires.
- Update this runbook with anything that was unclear.

## Contacts to fill in before launch

- Grievance officer / DPO: see `landing/site.ts`
- Supabase, DigitalOcean, Meta and Razorpay security contacts
- Lawyer on call

## Retention (for reference)

Set in `app_config.retention` and enforced hourly by the `jobs` role:
conversation text older than `message_days` (730) is deleted for everyone; a user who
sent STOP is erased after `opted_out_days` (180); unpaid orders expire after
`pending_order_hours` (48). "delete my data" erases immediately. Payment records
(`credit_ledger`, `orders`) and consent proof stay, attached to an anonymised user row.
Supabase backups age out on Supabase's own schedule (7 days on Pro): an erasure reaches
backups within that window.
