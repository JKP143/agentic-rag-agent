# Agentic RAG Agent — Workflow SOP

**Target workflow**: `REPLACE_ME_WORKFLOW_ID` ("RAG AI Agent Improvement in Project# 2&3") on the user's localhost:5678 n8n instance.

**What it does**: Ingests files from a watched Google Drive folder into **Neon Postgres** (vector chunks via pgvector for prose, JSONB rows for tabular data), then serves an agentic chat over Telegram that picks between four retrieval tools (semantic search, list documents, fetch full document, run SQL) based on the question.

Inspired by Cole Medin's [Ultimate RAG AI Agent V4 template](../n8n-plan/Ultimate_RAG_AI_Agent_V4.json); adapted to this stack (Gemini + Neon Postgres + Telegram).

---

## Prerequisites (do these BEFORE syncing the workflow JSON)

### 1. Apply the schema (Neon SQL Editor)

Open your Neon project → **SQL Editor**, run each file top-to-bottom:

- [sql/000_pgvector_and_documents.sql](../sql/000_pgvector_and_documents.sql) — enables `pgvector` extension; creates `documents` (id, text, metadata, embedding vector(1536)) with an IVFFlat similarity index and a jsonb `file_id` index
- [sql/001_document_metadata.sql](../sql/001_document_metadata.sql) — metadata table + `updated_at` trigger
- [sql/002_document_rows.sql](../sql/002_document_rows.sql) — rows table + indexes
- [sql/003_rag_readonly_role.sql](../sql/003_rag_readonly_role.sql) — grants for the read-only role

**Important**: OpenAI `text-embedding-3-small` emits 1536-dimensional vectors (the n8n Embeddings OpenAI node has `options.dimensions = 1536` set explicitly). If you ever switch embeddings model, you must drop and recreate the `embedding` column at the matching dimension AND re-embed everything. Both Embeddings nodes (`Embeddings OpenAI Insert` on the ingest path, `Embeddings OpenAI Retrieve` on the rag_search tool) MUST use the same model — otherwise query vectors and stored vectors live in different spaces and similarity search returns garbage.

### 2. Create the `rag_readonly` Postgres role in Neon

In the Neon SQL Editor, run:

```sql
create role rag_readonly login password '<pick-a-long-password>';
```

Then run the grants in `003_rag_readonly_role.sql` (it already references the `rag_readonly` role). Verify:

```sql
select has_table_privilege('rag_readonly', 'document_rows', 'SELECT');  -- t
select has_table_privilege('rag_readonly', 'document_rows', 'INSERT');  -- f
select has_table_privilege('rag_readonly', 'documents',     'INSERT');  -- f
```

Note: Neon treats additional roles as "branch roles" — they live in the branch you created them in (probably `production`). If you create a new branch, re-run step 2 there.

### 3. Create two n8n Postgres credentials

After the workflow JSON is synced in, the Postgres nodes will show unresolved placeholders; create these credentials and bind them in the n8n UI:

| Credential name | Role / password | Used by |
|---|---|---|
| `Neon Postgres (write)` | your default Neon role (or a dedicated write role) | 6 ingest nodes: `Delete Old Vector Chunks`, `Delete Old Doc Rows`, `Insert Document Metadata`, `Insert Table Rows`, `Update Schema for Document Metadata`, `Check If Exists in DB`, plus the 3 trashed-cleanup DELETE nodes, plus `Vector Store Insert` |
| `Neon Postgres (rag_readonly)` | `rag_readonly` / the password from step 2 | 4 tool nodes: `rag_search`, `list_documents`, `get_file_contents`, `query_document_rows` |

**Connection settings for both credentials** (from Neon Dashboard → your project → *Connect* → *Connection details*):

- Host: `<endpoint>.<region>.aws.neon.tech` (looks like `ep-...-pooler.ap-southeast-1.aws.neon.tech` for pooled, or without `-pooler` for direct)
- Database: `neondb` (default)
- Port: `5432`
- SSL: required (set SSL to `require`)
- Use the **pooled** endpoint for the write credential (handles connection churn from n8n executions); the direct endpoint also works for small traffic.

The `rag_search` node uses the read-only credential because PGVector retrieval only needs `SELECT` on `documents`.

### 4. (Optional) Use a separate Telegram bot for testing

During build, don't repoint your existing bot token — spin up a second bot via BotFather and bind it to the `Telegram Trigger` + `Telegram Send Message` nodes. Swap to the real bot token after verification.

### 5. Pick the Google Drive folder to watch

Replace the `folderToWatch` value in the two Drive triggers (Branch A and Branch B) with your target folder's Drive ID (the string in the URL when you open the folder).

---

## Architecture — four branches on one canvas

### Branch A — File Created ingest

```
Google Drive Trigger (fileCreated) → Loop Over Items → Set File ID → Download File
  → Switch on mimeType
      ├── xlsx   → Extract from Excel
      ├── gsheet → Extract from CSV (Drive pre-converts)
      ├── pdf    → Extract PDF Text
      ├── gdoc   → Extract Document Text
      └── other  → Extract Document Text (fallback)
  ├── tabular path → (a) Insert Table Rows (document_rows)
  │                  (b) Aggregate → Summarize → Set Schema → Upsert Schema
  └── unstructured → Postgres PGVector Store (Insert) [Default Data Loader + Recursive Splitter + Gemini Embeddings]
  → Upsert Document Metadata → Telegram "ingested ✓"
```

Design notes:
- `Loop Over Items` (SplitInBatches) serializes batch drops to avoid races on metadata upsert.
- `Set File ID` captures `file_id`, `file_title`, `file_url`, `file_type` from the trigger output — downstream nodes reference `$('Set File ID').item.json.file_id` to keep the file_id stable through the pipeline.
- The Vector Store loader writes `metadata.file_id` and `metadata.file_title` on every chunk — `get_file_contents` relies on this.
- For XLSX, each sheet becomes its own `dataset_id = <fileId>:<sheetName>` with its own `document_metadata` row and schema.

### Branch B — File Updated ingest

```
Google Drive Trigger (fileUpdated) → Loop Over Items → Set File ID
  → Delete Old Vector Chunks (DELETE FROM documents WHERE metadata->>'file_id' = $1)
  → Delete Old Doc Rows (DELETE FROM document_rows WHERE dataset_id LIKE $1 || '%')
  → [re-enter Branch A at Download File]
```

The `LIKE $1 || '%'` pattern covers both exact-match (non-tabular files) and the `fileId:sheet` suffix used by XLSX.

Metadata row is upserted by Branch A's tail — `updated_at` auto-bumps via the trigger created in `001_document_metadata.sql`.

### Branch C — Trashed-file cleanup (scheduled)

Google Drive has no native "File Deleted" trigger. We poll its trash every 15 minutes.

```
Schedule Trigger (every 15 min)
  → HTTP Request: GET files.list?q=trashed=true
  → Parse Trashed Files (Code node: one item per trashed file)
  → If Has Files → Check If Exists in DB (skip files we never ingested)
  → If Exists → Delete Old Vector Chunks
              → Delete Old Doc Rows
              → Delete Metadata (DELETE FROM document_metadata WHERE id = $1)
```

### Branch D — Chat agent

```
Telegram Trigger → AI Agent ─ Gemini Chat Model
                            ├ Simple Memory (window buffer — stateless across restarts for v1;
                            │   swap to Postgres Chat Memory if persistence needed)
                            ├ Tool: rag_search          (Supabase Vector Store retrieve-as-tool)
                            ├ Tool: list_documents      (Postgres Tool, rag_readonly)
                            ├ Tool: get_file_contents   (Postgres Tool, rag_readonly)
                            └ Tool: query_document_rows (Postgres Tool, rag_readonly)
  → Code in JavaScript (format output for Telegram)
  → Telegram Send Message
```

#### Agent tool contracts

| Tool | Type | Args | Returns |
|---|---|---|---|
| `rag_search` | Postgres PGVector Store (retrieve-as-tool) with Gemini embeddings | `query` (LLM-provided) | Top-6 chunks with `metadata.file_id`, `metadata.file_title` |
| `list_documents` | Postgres Tool (select) | — | All `document_metadata` rows: `id`, `title`, `mime_type`, `schema` |
| `get_file_contents` | Postgres Tool (executeQuery) | `file_id` (LLM-provided) | One row: concatenated `text` across all chunks for that file_id (PGVector column is `text`, not `content`) |
| `query_document_rows` | Postgres Tool (executeQuery) | `sql_query` (LLM-provided, SELECT only) | Whatever the query returns |

The `query_document_rows` tool's DESCRIPTION (shown to the LLM) must include example SQL — this is the single biggest retrieval-quality win. See the node parameters in the exported JSON.

#### System prompt

```
You are a personal assistant answering questions from a corpus of documents in Postgres.
Documents are either text-based (PDFs, Docs, text) or tabular (CSVs / Excel sheets).
You have four tools:
  - rag_search: semantic search on text chunks
  - list_documents: see what's available, including the `schema` column for tabular files
  - get_file_contents: full document text by file_id
  - query_document_rows: run SELECT SQL against the document_rows table for tabular math

Start with rag_search for fact-based questions. For averages, sums, counts, trends, or
anything requiring precise math over a CSV/Excel, use query_document_rows — first call
list_documents to find the matching dataset_id and read the schema. For "summarize X" or
"what's the overall message", use get_file_contents. Never guess a file_id / dataset_id;
always take it from list_documents. Only emit SELECT statements in query_document_rows.
If you don't find the answer, say so honestly — do not fabricate.

Heuristics:
  average / sum / count / top N / trend  → query_document_rows
  summarize / main points / overall      → get_file_contents
  specific fact / quote                  → rag_search
  compare across documents               → list_documents first, then parallel tool calls
```

---

## Build / sync procedure

From the project root:

```bash
# 1. Verify JSON is well-formed and has no structural breakage
python tools/verify_workflow.py workflows/rag-agent.n8n.json

# 2. Run the RAG-specific verifier (checks all four tools are wired, Switch has 5 outputs, etc.)
python tools/verify_rag_workflow.py workflows/rag-agent.n8n.json

# 3. Sync into the live n8n workflow
node .tmp/sync_workflow.js workflows/rag-agent.n8n.json REPLACE_ME_WORKFLOW_ID
```

Refresh the n8n canvas in the browser. Bind any newly-authored credentials (see Prerequisite 3) via the node detail panels. Toggle the workflow active.

---

## Verification (end-to-end)

1. **Schema**: confirm tables and role exist:
   ```sql
   \d+ document_metadata
   \d+ document_rows
   select rolname from pg_roles where rolname = 'rag_readonly';
   ```
2. **CSV ingest**: drop `sales_q1.csv` into the watched folder →
   - `document_metadata` has one row with non-null `schema`
   - `document_rows` has N rows with jsonb keys matching CSV headers
   - `documents` unchanged
3. **XLSX ingest (2 sheets)**:
   - Two `document_metadata` rows with `:<sheetName>` suffix
   - Rows split per sheet, each with its own schema
4. **PDF ingest**:
   - `document_metadata` row with null `schema`
   - `documents` has chunks with `metadata->>'file_id'` set
   - `document_rows` unchanged
5. **Update**: edit the CSV in Drive →
   - Old `document_rows` gone, new rows inserted
   - Metadata `updated_at` bumps, PK stable
6. **Delete via Drive trash**: move the PDF to trash → within 15 min, all 3 tables cleaned for that file_id
7. **Chat tool selection** (check the Executions view for the tool-call sequence):
   - "Average revenue in sales_q1?" → `list_documents` then `query_document_rows` (no `rag_search`)
   - "Summarize the handbook." → `list_documents` then `get_file_contents`
   - "What does the handbook say about PTO?" → `rag_search` only
   - "Compare Q1 revenue to the handbook's revenue targets." → both SQL and vector tools in one turn
8. **Safety**: Telegram prompt trying to coerce `DELETE` / `UPDATE` via `query_document_rows` → Postgres refuses at the role level; agent reports the failure honestly (no silent mutation)

---

## Operations / gotchas

### Credential management
- **Credentials don't transfer** across n8n instances or `sync_workflow.js` runs. The script updates `nodes` / `connections` / `settings` but never touches credential bindings, so once you bind them the first time they survive across re-syncs.
- **Webhook IDs are preserved** by `sync_workflow.js` — Telegram bot doesn't need re-registration after edits, as long as node IDs stay the same in the JSON.
- **Stale credential IDs survive delete-and-recreate**: if you delete a credential in the n8n UI and create a new one with the same name, every node that referenced the OLD credential keeps its OLD id (just the *name* in the UI updates). Symptom: `Credential with ID "..." does not exist for type "postgres"` when you test, even though the dropdown shows the right name. Fix: re-bind every affected node, either manually or via `.tmp/bind_credentials.py` which sweeps all Postgres + ElevenLabs nodes.
- **`rag_readonly` password rotation**: update the Postgres role password in Neon, then update the `Neon Postgres (rag_readonly)` credential in n8n. No workflow JSON change needed.

### Embeddings consistency
- **Embedding dimension is locked into the table**: the PGVector store's `embedding` column is declared with a fixed dimension (1536 for OpenAI `text-embedding-3-small`). Changing embedding models mid-flight gives you `expected N dimensions, not M` on insert. To switch: `DROP TABLE documents; CREATE TABLE documents (...embedding vector(<new-dim>)...);` re-create indexes, then re-ingest every file.
- **Insert and retrieve must use the same embedding model.** The Embeddings node feeding `Vector Store Insert` and the Embeddings node feeding `rag_search` (retrieve-as-tool) MUST be the same model with the same dimensions. Otherwise stored vectors and query vectors live in different spaces and cosine similarity returns garbage. Easy to forget when swapping models since the two nodes are far apart on the canvas.
- **`vector must have at least 1 dimension` is misleading**: this pgvector error fires when the Embeddings call returned an empty vector. The Embeddings node *itself* reports `success: true` because the upstream HTTP error was swallowed by langchain. Real cause is almost always an invalid/empty API key on the embeddings credential. Verify by hitting the model's REST endpoint directly with the same key (e.g. `https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key=...` or `https://api.openai.com/v1/embeddings`).

### n8n expression / node quirks
- **Postgres DELETE/EXECUTE nodes only output `{success: true}`**: any node downstream of a delete cannot read upstream JSON via `$json.X` — `$json` is just `{success: true}`. Use `$('Set File ID').item.json.X` (or whichever upstream node has the field) to reach back. This applies to `Insert Document Metadata` and any other node sitting after the two delete nodes.
- **`executeOnce: true` on terminal notification nodes**: anything after `Loop Over Items` receives one input item per chunk/row processed. Without `executeOnce`, you'll get 18 Telegram messages for an 18-chunk PDF. Set this on `Telegram Confirm Ingest` (and any future "ingestion done" notification) so it fires once per workflow execution.
- **Cole Medin's template uses `documents_pg`, ours uses `documents`**: copy-pasting his Postgres queries directly into our workflow creates silent no-ops, because his `IF EXISTS (SELECT 1 ... WHERE table_name = 'documents_pg')` guard is always false on our schema. When adapting Cole's queries, change every `documents_pg` to `documents` (in both the `IF EXISTS` check and the `EXECUTE` body).
- **Neon auto-suspend**: Free-tier Neon projects suspend compute after ~5 min of inactivity. The first request after suspension incurs ~1-2s cold-start. Not a bug — just a note if you see occasional slow tool calls.

### Sync discipline
- **`sync_workflow.js` does a full PUT** — it replaces nodes/connections wholesale. Anything you change in the n8n canvas between syncs is lost when the next sync runs. Treat `workflows/rag-agent.n8n.json` as the source of truth.
- **Preferred workflow**: tell Claude what to change in plain English; Claude edits the JSON; sync pushes it. Don't edit the canvas in parallel.
- **If you must canvas-edit**: tell Claude before the next sync so the live state can be pulled into JSON first.
- **Never test API behaviour with skinny PUTs against the live workflow**: a PUT with `nodes: []` will wipe every node. n8n accepts it without warning. The JSON-first discipline saved us from a bad outcome — the workflow was restored in seconds — but don't rely on that.

### Postgres Chat Memory + Telegram Trigger
- The default `sessionIdType: fromInput` looks for `$json.sessionId` (which `@n8n/n8n-nodes-langchain.chatTrigger` provides). Telegram Trigger does not.
- Required config for Telegram-driven chat:
  - `sessionIdType: customKey`
  - `sessionKey: =rag_v2:{{ $('Telegram Trigger').item.json.message.chat.id }}` (the `rag_v2:` prefix namespaces this workflow's chat history off any prior workflow's history that may share `n8n_chat_histories`)
  - `tableName: n8n_chat_histories` (default; auto-created on first write)

### `availableInMCP` setting
- The n8n public REST API has a strict schema for `settings` and explicitly rejects `availableInMCP`, `binaryMode`, `callerPolicy` as "additional properties". When `sync_workflow.js` does a PUT without those fields, n8n resets them to defaults — `availableInMCP` flips to `false`.
- Workaround built into `sync_workflow.js`: set `N8N_PRESERVE_AVAILABLE_IN_MCP=true` plus `N8N_EMAIL` / `N8N_PASSWORD` in `.env`. After each PUT, the script logs into `/rest/login`, captures the session cookie, and PATCHes `/rest/mcp/workflows/{id}/toggle-access` with `{availableInMCP: true}` to re-enable.
- The dedicated MCP toggle endpoint is **session-cookie auth only** — the `X-N8N-API-KEY` header is rejected with 401 against `/rest/`. Hence the cookie-based path.

### Optional / deferred
- **Agentic chunking + Cohere reranker** (both in Cole's template) are deferred to v2. Revisit if retrieval quality is poor on real questions.
- **Dedicated `rag_readonly` Postgres role**: SQL is in `sql/003_rag_readonly_role.sql` but the role/credential have not been created yet. Currently all 14 Postgres nodes share the `NeonDB` (write) credential. The `query_document_rows` agent tool could in theory generate a malicious DELETE if the LLM were prompted-injected — system prompt limits it to `SELECT` but that's a soft constraint. Hardening = create the role and re-run `READONLY_CRED_ID=<id> python .tmp/bind_credentials.py`.

---

## Out of scope (v1)

- Modifying the existing basic RAG workflow — stays running until this one is validated.
- Repointing the Telegram bot from old chat to new — manual swap after verification.
- OCR / image extraction from PDFs (text-only for v1).
- Tuning chunk size, topK, and system prompt — ship defaults, tune from real usage.
