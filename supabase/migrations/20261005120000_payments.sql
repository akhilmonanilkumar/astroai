-- M7: in-chat payments (Razorpay through WhatsApp's native order_details checkout).
--
-- An order is created when the user picks a pack or pass; its reference_id goes to
-- WhatsApp and becomes the Razorpay order's receipt. Webhooks (WhatsApp payment status,
-- Razorpay) only queue a check; the payment worker confirms with Razorpay's API, then
-- marks the order paid and credits it in one transaction, exactly once.

create table orders (
  reference_id  text primary key,               -- ours, <= 35 chars (WhatsApp limit)
  user_id       uuid not null references users(id) on delete restrict,
  kind          text not null check (kind in ('pack', 'pass')),
  item_id       text not null,                  -- app_config packs[].id / passes[].id
  amount_paise  integer not null check (amount_paise > 0),
  prashnas      integer,                        -- packs
  days          integer,                        -- passes
  status        text not null default 'pending'
                  check (status in ('pending', 'paid', 'failed', 'expired')),
  payment_id    text unique,                    -- Razorpay payment id once paid
  created_at    timestamptz not null default now(),
  paid_at       timestamptz,
  check ((kind = 'pack' and prashnas > 0) or (kind = 'pass' and days > 0))
);
create index orders_user_idx on orders (user_id, created_at desc);
create index orders_pending_idx on orders (created_at) where status = 'pending';
alter table orders enable row level security;
