-- M6: admin console.
--
-- The console (Next.js) signs in with Supabase Auth (email + password, then TOTP MFA) and
-- talks only to the backend admin API, which checks the JWT (aal2) against `admins`.
-- Personal fields are encrypted by the app and the keys live in the backend, so the
-- console gets no RLS policies: every table stays deny-all for anon/authenticated.

create type admin_role as enum ('owner', 'agent');

-- Team members allowed into the console. owner: everything; agent: inbox and replies.
create table admins (
  id           uuid primary key default gen_random_uuid(),
  email        text not null unique check (email = lower(email)),
  role         admin_role not null default 'agent',
  created_at   timestamptz not null default now(),
  disabled_at  timestamptz
);
alter table admins enable row level security;

-- Human team replies are messages with sent_by = 'human'; this says who sent them.
alter table messages add column sent_by_admin uuid references admins(id);

-- Who closed a case (e.g. "admin:<id>"), and why.
alter table escalations add column resolved_by text;

-- Users list: newest first, and the 24-hour reply window (last message from the user).
create index users_created_idx on users (created_at desc);
create index messages_user_in_idx on messages (user_id, created_at desc) where direction = 'in';

-- Approved WhatsApp utility template for team replies after the 24-hour window closes.
-- Languages map the user's language to the template's approved language code.
insert into app_config (key, value) values
  ('human_template', '{"name": "team_followup", "languages": {"en": "en", "hinglish": "en", "hi": "hi"}}')
on conflict (key) do nothing;
