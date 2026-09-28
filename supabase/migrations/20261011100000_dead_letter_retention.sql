-- Failed jobs in Redis (q:dead) hold phone numbers and message text; the retention sweep
-- now trims them after retention.dead_letter_days.

update app_config
set value = value || '{"dead_letter_days": 14}'::jsonb
where key = 'retention' and not (value ? 'dead_letter_days');
