-- Agentic RAG: document metadata table.
-- One row per ingested file (or per sheet for XLSX: id = "<fileId>:<sheetName>").
create table if not exists document_metadata (
  id          text primary key,
  title       text not null,
  url         text,
  mime_type   text,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now(),
  schema      text
);

create or replace function document_metadata_touch_updated_at()
returns trigger as $$
begin
  new.updated_at := now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists document_metadata_updated_at on document_metadata;
create trigger document_metadata_updated_at
  before update on document_metadata
  for each row execute function document_metadata_touch_updated_at();
