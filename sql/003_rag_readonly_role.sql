-- Agentic RAG: read-only role for the AI Agent's SQL tools.
-- Used by rag_search / list_documents / get_file_contents / query_document_rows.
--
-- IMPORTANT: replace <PASTE_LONG_PASSWORD_HERE> with a real password you generate
-- (e.g. `openssl rand -base64 32`) BEFORE running this file. Save the password in
-- a password manager - you will paste it into the n8n Postgres credential for
-- `Neon Postgres (rag_readonly)`.

create role rag_readonly with login password '<PASTE_LONG_PASSWORD_HERE>';

grant usage on schema public to rag_readonly;
grant select on document_metadata, document_rows, documents to rag_readonly;

alter role rag_readonly set statement_timeout = '5s';

-- Safety checks: run these after the grants to confirm the role cannot mutate.
-- Expected results in the comment on each line.
--   select has_table_privilege('rag_readonly', 'document_rows', 'SELECT');  -- t
--   select has_table_privilege('rag_readonly', 'document_rows', 'INSERT');  -- f
--   select has_table_privilege('rag_readonly', 'documents',     'INSERT');  -- f
--   select has_table_privilege('rag_readonly', 'document_metadata', 'UPDATE'); -- f
