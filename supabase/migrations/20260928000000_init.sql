-- Guruji v1 schema: users, consent, birth data, charts, messages, credits, escalations,
-- attribution, config, audit. Backend connects with the service role; RLS is enabled on
-- every table with no policies, so anon/authenticated clients see nothing.
-- Admin console policies (MFA-gated) arrive in M6.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create or replace function forbid_mutation() returns trigger
language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name;
end $$;

-- ---------------------------------------------------------------------------
-- users
-- wa_id (phone number) is PII: stored AES-GCM encrypted by the app, looked up by
-- HMAC-SHA256 hash. On "delete my data" the row is anonymised (enc fields nulled,
-- hash replaced) rather than deleted, because credit_ledger rows must be retained
-- for GST/accounting and reference it.
-- ---------------------------------------------------------------------------
create type user_state as enum ('new', 'consented', 'onboarding', 'active', 'escalated', 'blocked');

create table users (
  id           uuid primary key default gen_random_uuid(),
  wa_id_hash   text not null unique,
  wa_id_enc    bytea,
  display_name_enc bytea,
  language     text,
  state        user_state not null default 'new',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  deleted_at   timestamptz
);
create trigger users_updated_at before update on users
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- consent (DPDP): proof = notice version + the button reply's wamid + timestamp
-- ---------------------------------------------------------------------------
create table consents (
  id              bigint generated always as identity primary key,
  user_id         uuid not null references users(id) on delete cascade,
  notice_version  text not null,
  purpose         text not null check (purpose in ('readings', 'marketing')),
  granted         boolean not null,
  age_confirmed   boolean not null,
  wamid           text not null,
  given_at        timestamptz not null,
  created_at      timestamptz not null default now()
);
create index consents_user_idx on consents (user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- birth details: every personal field encrypted by the app (per-field keys)
-- ---------------------------------------------------------------------------
create table birth_details (
  user_id         uuid primary key references users(id) on delete cascade,
  name_enc        bytea,
  birth_date_enc  bytea not null,
  birth_time_enc  bytea,
  time_known      boolean not null default true,
  place_label_enc bytea not null,
  latitude_enc    bytea not null,
  longitude_enc   bytea not null,
  tz_name         text not null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create trigger birth_details_updated_at before update on birth_details
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- charts: computed dossier (D1, D9, dasha, yogas, transits) per engine version
-- ---------------------------------------------------------------------------
create table charts (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references users(id) on delete cascade,
  engine_version  text not null,
  dossier         jsonb not null,
  created_at      timestamptz not null default now(),
  unique (user_id, engine_version)
);

-- ---------------------------------------------------------------------------
-- messages (conversation log)
-- ---------------------------------------------------------------------------
create table messages (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references users(id) on delete cascade,
  direction   text not null check (direction in ('in', 'out')),
  sent_by     text not null check (sent_by in ('user', 'guru', 'human')),
  wamid       text unique,
  turn_id     text,
  kind        text not null,
  body        text,
  created_at  timestamptz not null default now()
);
create index messages_user_idx on messages (user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- credits: append-only ledger; balance = sum(delta)
-- ---------------------------------------------------------------------------
create table credit_ledger (
  id               bigint generated always as identity primary key,
  user_id          uuid not null references users(id) on delete restrict,
  delta            integer not null check (delta <> 0),
  reason           text not null check (reason in
                     ('welcome', 'daily_free', 'purchase', 'pass', 'spend', 'refund', 'adjust', 'expire')),
  idempotency_key  text not null unique,
  ref              jsonb,
  created_at       timestamptz not null default now()
);
create index credit_ledger_user_idx on credit_ledger (user_id);
create trigger credit_ledger_append_only before update or delete on credit_ledger
  for each row execute function forbid_mutation();

create view credit_balances with (security_invoker = true) as
  select user_id, sum(delta)::integer as balance
  from credit_ledger
  group by user_id;

-- ---------------------------------------------------------------------------
-- human escalation
-- ---------------------------------------------------------------------------
create type escalation_status as enum ('open', 'acknowledged', 'resolved', 'handed_back');

create table escalations (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references users(id) on delete cascade,
  category         text not null check (category in
                     ('crisis', 'medical', 'legal', 'abuse', 'human_requested',
                      'dissatisfied', 'payment_dispute', 'out_of_scope')),
  severity         smallint not null check (severity between 1 and 3),
  status           escalation_status not null default 'open',
  alert_count      integer not null default 0,
  opened_at        timestamptz not null default now(),
  acknowledged_at  timestamptz,
  resolved_at      timestamptz,
  notes            text
);
create index escalations_active_idx on escalations (opened_at)
  where status in ('open', 'acknowledged');

-- ---------------------------------------------------------------------------
-- Click-to-WhatsApp ad attribution (referral object on the first message)
-- ---------------------------------------------------------------------------
create table ad_referrals (
  id           bigint generated always as identity primary key,
  user_id      uuid not null references users(id) on delete cascade,
  source_type  text,
  source_id    text,
  ctwa_clid    text,
  headline     text,
  raw          jsonb not null,
  created_at   timestamptz not null default now()
);
create index ad_referrals_source_idx on ad_referrals (source_id);

-- ---------------------------------------------------------------------------
-- business config (prices, limits, flags), editable from the admin console
-- ---------------------------------------------------------------------------
create table app_config (
  key         text primary key,
  value       jsonb not null,
  updated_at  timestamptz not null default now(),
  updated_by  text not null default 'migration'
);
create trigger app_config_updated_at before update on app_config
  for each row execute function set_updated_at();

insert into app_config (key, value) values
  ('packs', '[
     {"id": "trial", "price_inr": 11,  "prashnas": 2,   "show_once": true},
     {"id": "p51",   "price_inr": 51,  "prashnas": 10},
     {"id": "p101",  "price_inr": 101, "prashnas": 25,  "badge": "Most chosen"},
     {"id": "p251",  "price_inr": 251, "prashnas": 70},
     {"id": "p501",  "price_inr": 501, "prashnas": 160}
   ]'),
  ('passes', '[
     {"id": "plus_monthly",   "price_inr": 199,  "days": 30},
     {"id": "plus_quarterly", "price_inr": 501,  "days": 90},
     {"id": "plus_yearly",    "price_inr": 1501, "days": 365}
   ]'),
  ('free_tier', '{"welcome_hours": 72, "welcome_prashnas": 5, "daily_free_answers": 1}'),
  ('prashna', '{"followups": 3, "voice_credit_cost": 2}'),
  ('plus_limits', '{"prashnas_per_day": 5}'),
  ('flags', '{"busy_mode": false, "voice_enabled": true, "new_user_admission": true}');

-- ---------------------------------------------------------------------------
-- audit log: no FK so entries survive user deletion
-- ---------------------------------------------------------------------------
create table audit_log (
  id               bigint generated always as identity primary key,
  actor            text not null,
  action           text not null,
  subject_user_id  uuid,
  detail           jsonb,
  created_at       timestamptz not null default now()
);
create index audit_log_subject_idx on audit_log (subject_user_id, created_at desc);
create trigger audit_log_append_only before update or delete on audit_log
  for each row execute function forbid_mutation();

-- ---------------------------------------------------------------------------
-- RLS: deny by default for anon/authenticated; service role bypasses RLS
-- ---------------------------------------------------------------------------
alter table users          enable row level security;
alter table consents       enable row level security;
alter table birth_details  enable row level security;
alter table charts         enable row level security;
alter table messages       enable row level security;
alter table credit_ledger  enable row level security;
alter table escalations    enable row level security;
alter table ad_referrals   enable row level security;
alter table app_config     enable row level security;
alter table audit_log      enable row level security;
