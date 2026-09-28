-- Hourly payment reconciliation re-checks recent unpaid orders (pending, failed or expired)
-- with Razorpay, so a payment whose webhook never arrived is still credited.

create index orders_unpaid_idx on orders (created_at) where status <> 'paid';
