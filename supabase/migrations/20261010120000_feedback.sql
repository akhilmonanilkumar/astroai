-- M8: how users rated Guruji's answers (a 👎 or 👍/❤️/🙏 reaction on a reply), for daily
-- persona tuning. One row per user and answer turn; a later reaction replaces the rating.
-- No text here: the console joins the question and answer from `messages`, so retention
-- and erasure of messages apply to what the team can read.

create table feedback (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references users(id) on delete cascade,
  turn_id     text not null,          -- the answer's turn (messages.turn_id)
  rating      text not null check (rating in ('up', 'down')),
  created_at  timestamptz not null default now(),
  unique (user_id, turn_id)
);
create index feedback_created_idx on feedback (created_at desc);
create index feedback_down_idx on feedback (created_at desc) where rating = 'down';

alter table feedback enable row level security;
