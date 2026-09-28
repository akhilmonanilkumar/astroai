-- M7: DPDP controls. "STOP" opts a user out of all messages (data kept, see retention);
-- "delete my data" erases everything personal. Payment records (credit_ledger, orders)
-- stay for tax law, attached to an anonymised user row (see guruji.db erase_user).

alter type user_state add value if not exists 'opted_out';

-- Retention (days), enforced hourly by the jobs role:
--   opted_out_days      an opted-out user's data is erased this long after STOP
--   message_days        conversation text older than this is removed for everyone
--   pending_order_hours unpaid orders expire after this
insert into app_config (key, value) values
  ('retention', '{"opted_out_days": 180, "message_days": 730, "pending_order_hours": 48}')
on conflict (key) do nothing;

alter table users add column opted_out_at timestamptz;

create index messages_created_idx on messages (created_at);
