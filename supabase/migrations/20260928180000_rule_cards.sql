-- M4: rule cards for retrieval (factor-tagged, hybrid vector + full-text search).
-- The corpus lives in the repo (backend/src/guruji/rag/corpus/*.toml); workers upsert it
-- here by content hash (guruji.rag.index.sync_cards). No personal data in this table.

create extension if not exists vector;

-- array_to_string is only STABLE, which generated columns reject; for text[] it is safe.
create or replace function words_of(text[]) returns text
language sql immutable parallel safe as $$ select array_to_string($1, ' ') $$;

create table rule_cards (
  id            text primary key,
  title         text not null,
  body          text not null,
  factors       text[] not null default '{}',
  topics        text[] not null default '{}',
  content_hash  text not null,
  model         text not null,
  -- Dimension left open so the embedding model can change without a migration. The
  -- corpus is a few hundred rows, so exact scans beat an ANN index here.
  embedding     vector not null,
  tsv           tsvector generated always as (
                  setweight(to_tsvector('english'::regconfig, title), 'A') ||
                  setweight(to_tsvector('english'::regconfig, words_of(topics)), 'B') ||
                  setweight(to_tsvector('english'::regconfig, body), 'C')
                ) stored,
  updated_at    timestamptz not null default now()
);
create index rule_cards_factors_idx on rule_cards using gin (factors);
create index rule_cards_tsv_idx on rule_cards using gin (tsv);

alter table rule_cards enable row level security;
