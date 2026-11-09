-- A transcription the user deleted whose files could not be removed yet (a file Windows
-- still held: an antivirus scan, an indexer). The row is hidden as if gone, never runs
-- again, and the worker removes it once the files can go (ClickUp z8tj1hwqq1). Also set
-- while a running job is being stopped for a delete, so a crash does not bring it back.
ALTER TABLE transcriptions ADD COLUMN delete_pending INTEGER NOT NULL DEFAULT 0;
