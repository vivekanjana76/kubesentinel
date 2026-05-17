-- Phase 2: RAG memory — runbooks table, HNSW index, match function
-- Idempotent: safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS runbooks (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT        NOT NULL,
    source_file  TEXT        NOT NULL,
    chunk_index  INTEGER     NOT NULL,
    content      TEXT        NOT NULL,
    embedding    VECTOR(384) NOT NULL,
    metadata     JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Unique constraint used for idempotent upsert by ingest.py
    CONSTRAINT runbooks_source_chunk_uq UNIQUE (source_file, chunk_index)
);

-- HNSW index for fast approximate nearest-neighbour search using cosine distance.
-- ef_construction=128 and m=16 are sensible defaults for a small corpus (<100k rows).
CREATE INDEX IF NOT EXISTS runbooks_embedding_hnsw_idx
    ON runbooks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- match_runbooks: returns the top-k most similar runbook chunks for a query embedding.
-- Called by retriever.py via supabase.rpc("match_runbooks", {...}).
CREATE OR REPLACE FUNCTION match_runbooks(
    query_embedding VECTOR(384),
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    id          UUID,
    title       TEXT,
    source_file TEXT,
    content     TEXT,
    similarity  FLOAT,
    metadata    JSONB
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.id,
        r.title,
        r.source_file,
        r.content,
        1 - (r.embedding <=> query_embedding) AS similarity,
        r.metadata
    FROM runbooks r
    ORDER BY r.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
