-- What the redesign needed that the rows could not yet say (D50–D55).
--
-- Action items gain the three things the inbox has to sort and act on, plus a record of
-- who wrote the row:
--
-- `detail` is the lighter second line — why it matters, what it unblocks, who is
-- waiting. The model writes it; a user-added item may carry one too.
--
-- `due_at` is a calendar date (YYYY-MM-DD). `due` stays what was *said* ("by Thursday");
-- this is what it means. It is NULL on every row written before this migration, and the
-- application resolves those lazily from `due` against the meeting's own date rather
-- than backfilling here: the resolver is Python (app/due.py), and a migration that calls
-- into application code is a migration that cannot be replayed once that code changes.
--
-- `snoozed_until` is the user's, like `done_at`, and survives a re-summarize the same way.
--
-- `source` is 'model' or 'user'. Re-summarizing replaces the model's rows and must never
-- touch the user's: an item someone typed in by hand is not the model's to take away.
ALTER TABLE action_items ADD COLUMN detail TEXT;
ALTER TABLE action_items ADD COLUMN due_at TEXT;
ALTER TABLE action_items ADD COLUMN snoozed_until TEXT;
ALTER TABLE action_items ADD COLUMN source TEXT NOT NULL DEFAULT 'model';

CREATE INDEX ix_action_items_due ON action_items(due_at);

-- Tags are the user's own words for a meeting. Unique per meeting case-insensitively;
-- the application normalises and dedupes before writing (Dao.set_tags), so the stored
-- spelling is the one the user first typed.
CREATE TABLE meeting_tags (
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  tag        TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, tag)
);
CREATE INDEX ix_meeting_tags_tag ON meeting_tags(tag);

-- A JSON object from a transcript speaker slot ("THEM", "THEM_1", …) to the name the user
-- says belongs to it. The slots are the recorder's and the diariser's; the names are the
-- user's, laid over them at read time. Nothing in the transcript is rewritten.
ALTER TABLE meetings ADD COLUMN speaker_names TEXT;
