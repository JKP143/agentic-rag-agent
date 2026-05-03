-- Agentic RAG: tabular row storage.
-- dataset_id references document_metadata.id (which may be "<fileId>:<sheet>" for XLSX).
create table if not exists document_rows (
  id          bigserial primary key,
  dataset_id  text references document_metadata(id) on delete cascade,
  row_data    jsonb not null
);

create index if not exists document_rows_dataset_idx
  on document_rows (dataset_id);

create index if not exists document_rows_row_data_gin
  on document_rows using gin (row_data jsonb_path_ops);
