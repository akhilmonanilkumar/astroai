-- Refunds, disputes and double payments (PR-10).
--
-- A payment refunded at Razorpay (from the dashboard, or a chargeback the bank rules
-- against us) marks its order 'refunded': the pack's unused credits are taken back (never
-- below a zero balance) and a refunded Guru Plus pass ends. Disputes, double payments and
-- partial refunds are not acted on automatically: they become payment_issues rows, which
-- the team sees in the console (and on Telegram) and settles from the Razorpay dashboard.
-- No personal data here: order and Razorpay ids, amounts, statuses.

alter table orders drop constraint orders_status_check;
alter table orders add constraint orders_status_check
  check (status in ('pending', 'paid', 'failed', 'expired', 'refunded'));
alter table orders add column refunded_at timestamptz;

create table payment_issues (
  id            bigint generated always as identity primary key,
  key           text not null unique,       -- dedupes webhook repeats, e.g. dispute:<id>:created
  kind          text not null check (kind in ('dispute', 'duplicate', 'partial_refund')),
  reference_id  text references orders(reference_id),
  payment_id    text not null,
  amount_paise  integer,
  details       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz,
  resolved_by   text                        -- "admin:<id>"
);
create index payment_issues_open_idx on payment_issues (created_at desc)
  where resolved_at is null;

alter table payment_issues enable row level security;
