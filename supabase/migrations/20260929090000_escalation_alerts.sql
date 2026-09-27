-- M5: alert bookkeeping for escalations (Telegram re-pings of unacknowledged crises).

alter table escalations add column last_alerted_at timestamptz;
alter table escalations add column acknowledged_by text;  -- e.g. "telegram:<user id>", no PII
-- The user's state when it opened; resolving returns them there (e.g. mid-onboarding).
alter table escalations add column prior_state user_state not null default 'active';

-- At most one active escalation per user; new signals update it instead of stacking.
create unique index escalations_one_active_idx on escalations (user_id)
  where status in ('open', 'acknowledged');
