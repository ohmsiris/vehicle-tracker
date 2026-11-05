-- v44: the review screen (scan beside the lines). Applied 5 Nov 2026 via MCP (migration schema_v44_acc_docs_review).
alter table acc_docs add column if not exists image_path text;   -- storage key of the page scan (served through the app, not a public link)
alter table acc_docs add column if not exists edited_at timestamptz;  -- lines fixed on the review screen: a re-import refuses to overwrite
