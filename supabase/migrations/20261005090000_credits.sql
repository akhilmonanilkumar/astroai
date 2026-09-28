-- M7: credit metering. The ledger (credit_ledger, append-only) already exists; this adds
-- the per-user meter and Guru Plus passes.

-- What the user has used of their free and paid allowances; see guruji.agent.metering:
-- {"prashna": {"id", "opened", "followups_left", "paid_by"}, "welcome_used",
--  "day", "day_plus_used", "day_free_used", "spend_ok_day", "pending": {...}}
-- "pending" holds a question waiting for the user to accept its cost (personal data, like
-- messages.body, and removed with the user's data).
alter table users add column meter jsonb not null default '{}'::jsonb;

-- Guru Plus: prepaid passes (M7 payments create them; renewals extend them).
create table passes (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references users(id) on delete cascade,
  plan_id     text not null,
  starts_at   timestamptz not null,
  ends_at     timestamptz not null check (ends_at > starts_at),
  source      text not null,          -- e.g. "payment:<razorpay payment id>", "admin:<id>"
  created_at  timestamptz not null default now(),
  unique (source)
);
create index passes_user_idx on passes (user_id, ends_at desc);
alter table passes enable row level security;

-- Spends are keyed "spend:<turn_id>"; a thumbs-down finds the spend by that key.
-- (credit_ledger.idempotency_key is already unique, so lookups by key are indexed.)

-- How long a prashna stays open for follow-ups.
update app_config
   set value = value || '{"followup_hours": 12}'::jsonb
 where key = 'prashna' and not value ? 'followup_hours';
