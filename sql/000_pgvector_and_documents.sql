-- Agentic RAG on Neon: enable pgvector and create the vector-store table.
-- Apply this BEFORE 001/002/003.
--
-- The n8n "Postgres PGVector Store" node (langchain) reads and writes this table.
-- Column names (text, metadata, embedding) are fixed by the langchain PGVector integration.
-- Do NOT rename them.

create extension if not exists vector;

create table if not exists documents (
  id         bigserial primary key,
  text       text,
  metadata   jsonb,
  embedding  vector(1536)    -- OpenAI text-embedding-3-small at 1536 dims
                             -- (set "dimensions" in the n8n Embeddings OpenAI node options to match)
);

-- Similarity-search index. IVFFlat is fine for <1M rows; switch to HNSW later if needed.
-- Tune `lists` to ~sqrt(total row count) once you know your corpus size.
create index if not exists documents_embedding_idx
  on documents using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- jsonb filter index — speeds up queries like metadata->>'file_id' = $1.
create index if not exists documents_metadata_file_id_idx
  on documents ((metadata->>'file_id'));
