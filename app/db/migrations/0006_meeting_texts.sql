-- The summary as plain text, so search can reach it (D61, assistant plan step 2).
--
-- The summary itself stays a file (`summary.html`, written by the render stage); this is
-- a searchable copy, rewritten whenever the summary is. `kind` leaves room for other
-- texts of a meeting; today it is only 'summary'. An empty `text` records that a
-- meeting was checked and has no summary, so the start-up backfill reads each folder once.
CREATE TABLE meeting_texts (
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, kind)
);
