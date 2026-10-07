-- Run on db-host (DB_HOST):
--   docker exec -i shared-postgres psql -U admin < schema_bootstrap.sql   (role + db, see README)
--   docker exec -i shared-postgres psql -U leegstand -d leegstand < schema.sql

CREATE TABLE IF NOT EXISTS publications (
    id              bigserial PRIMARY KEY,
    url             text        NOT NULL,
    url_hash        char(64)    NOT NULL UNIQUE,      -- dedup, same idea as jobhunter
    source          text        NOT NULL,             -- officielebekendmakingen | googlenews
    doc_type        text        NOT NULL CHECK (doc_type IN ('official','news')),
    publisher       text,
    title_nl        text        NOT NULL,
    published_at    date,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    lang            text        NOT NULL DEFAULT 'nl',
    text_nl         text,                              -- original kept verbatim
    text_source     text,                              -- page | snippet | title
    summary_en      text,
    summary_ru      text,                              -- stage 2, after RTX 3090
    facts           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    flags           text[]      NOT NULL DEFAULT '{}',
    relevant        boolean,
    reported_at     timestamptz                        -- NULL = not yet sent to Telegram
);

CREATE INDEX IF NOT EXISTS publications_published_idx ON publications (published_at DESC);
CREATE INDEX IF NOT EXISTS publications_unreported_idx ON publications (reported_at) WHERE reported_at IS NULL;
CREATE INDEX IF NOT EXISTS publications_municipality_idx ON publications ((facts->>'municipality'));
