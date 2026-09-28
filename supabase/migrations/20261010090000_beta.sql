-- M8: closed beta. With app_config beta.invite_only (or flags.new_user_admission off), a
-- new user needs an invite code before onboarding; the admitting code is kept here.
-- Users who have consented are never waitlisted.

alter table users add column admitted_at timestamptz;
alter table users add column invite_code text;

insert into app_config (key, value) values
  ('beta', '{"invite_only": false, "codes": []}')
on conflict (key) do nothing;
