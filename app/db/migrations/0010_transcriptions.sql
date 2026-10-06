-- File transcription as a local service (D86). A file job is one unit of work with no
-- stages and no meeting, so it gets its own table: jobs.meeting_id is a NOT NULL foreign
-- key, which SQLite cannot drop without rebuilding the table, and every jobs query
-- assumes a meeting.
CREATE TABLE transcriptions (
  id            TEXT PRIMARY KEY,           -- 'tr_' + 12 base32 characters; opaque
  state         TEXT NOT NULL,              -- pending|running|done|failed|cancelled
  source_name   TEXT NOT NULL,              -- the original file name, for display only
  source_kind   TEXT NOT NULL,              -- upload|path
  source_path   TEXT NOT NULL,              -- what the engine reads
  size_bytes    INTEGER,
  duration_s    REAL,
  options       TEXT NOT NULL,              -- JSON: language, diarize, prompt, max_words_per_cue
  client        TEXT NOT NULL,              -- ui|api|mcp
  language      TEXT,
  language_conf REAL,
  model         TEXT,                       -- JSON: the backend that transcribed it
  phase         TEXT,                       -- decode|check|language|transcribe|diarize
  progress      REAL NOT NULL DEFAULT 0,    -- 0-1 across the whole job, weighted by phase
  attempts      INTEGER NOT NULL DEFAULT 0,
  not_before    TEXT,
  last_error    TEXT,
  queued_at     TEXT NOT NULL,              -- the FIFO key it shares with meeting jobs
  created_at    TEXT NOT NULL,
  started_at    TEXT,
  finished_at   TEXT,
  updated_at    TEXT NOT NULL
);
CREATE INDEX ix_transcriptions_runnable ON transcriptions(state, not_before, queued_at);
CREATE INDEX ix_transcriptions_created ON transcriptions(created_at);

-- The meeting side of the same FIFO key: set when a meeting enters the queue (a
-- recording, an import, a retry, "Transcribe again") and inherited by its later stages,
-- so its summary keeps its place. Keyed by the earliest job row instead, an old
-- meeting's re-run would jump the queue, since its rows outlive the run. Nullable:
-- ADD COLUMN cannot add NOT NULL without a default; every write sets it from now on.
ALTER TABLE jobs ADD COLUMN queued_at TEXT;
UPDATE jobs SET queued_at = created_at;
