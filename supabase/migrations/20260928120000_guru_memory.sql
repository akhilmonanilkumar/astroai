-- M3: onboarding progress, guru memory (life facts + readings ledger), idempotent turns.

-- Onboarding progress while users.state is 'new' / 'consented' / 'onboarding':
-- {"step": "...", "name": ..., "date": ..., "time": ..., "place_candidates": [...]}
-- Personal values in here are app-encrypted like birth_details (see guruji.crypto).
alter table users add column onboarding jsonb not null default '{}'::jsonb;

-- Interactive parts of a message (reply buttons shown / chosen), e.g. {"buttons": [...]}.
alter table messages add column meta jsonb;

-- One guru reply per turn: a redelivered turn must not log (or act) twice.
create unique index messages_out_turn_idx on messages (turn_id)
  where direction = 'out' and turn_id is not null;

-- ---------------------------------------------------------------------------
-- life facts: what the user has told Guruji about their life ("visible memory")
-- ---------------------------------------------------------------------------
create table life_facts (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references users(id) on delete cascade,
  category    text not null check (category in
                ('career', 'relationship', 'family', 'health', 'education', 'finance',
                 'relocation', 'spiritual', 'other')),
  fact        text not null,
  turn_id     text not null,
  seq         smallint not null,
  created_at  timestamptz not null default now(),
  unique (turn_id, seq)
);
create index life_facts_user_idx on life_facts (user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- readings ledger: what Guruji said, so he never contradicts himself
-- ---------------------------------------------------------------------------
create table readings (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references users(id) on delete cascade,
  topic       text not null,
  summary     text not null,
  factors     text[] not null default '{}',
  turn_id     text not null,
  seq         smallint not null,
  created_at  timestamptz not null default now(),
  unique (turn_id, seq)
);
create index readings_user_idx on readings (user_id, created_at desc);

alter table life_facts enable row level security;
alter table readings   enable row level security;
