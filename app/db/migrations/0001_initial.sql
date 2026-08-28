-- migration 0001: initial schema. Kept identical to ../schema.sql; a test asserts it.
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE meetings (
  id              TEXT PRIMARY KEY,          -- '2026-08-28_1400_a1b2c3'
  folder          TEXT NOT NULL,             -- absolute path
  source          TEXT NOT NULL,             -- manual|detected|imported|calendar
  state           TEXT NOT NULL,             -- see states.py
  title           TEXT,
  title_source    TEXT,                      -- window|llm|calendar|user
  language        TEXT,                      -- detected meeting language, e.g. 'he'|'en'
  language_conf   REAL,                      -- detection probability; <0.6 -> default used
  summary_language TEXT,                     -- resolved at summarize time
  started_at      TEXT NOT NULL,             -- ISO8601 local w/ offset
  ended_at        TEXT,
  duration_s      INTEGER,
  profile         TEXT NOT NULL,             -- gpu-live|cpu-deferred|remote-worker
  sensitive       INTEGER NOT NULL DEFAULT 0,
  evidence_json   TEXT,                      -- why it was recorded (detected only)
  calendar_json   TEXT,                      -- enrichment payload, verbatim; NULL in V1
  error           TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX ix_meetings_started ON meetings(started_at DESC);
CREATE INDEX ix_meetings_state   ON meetings(state);

CREATE TABLE jobs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL,        -- transcribe|assemble|summarize|render|deliver
  state        TEXT NOT NULL,        -- pending|running|done|failed|cancelled
  attempts     INTEGER NOT NULL DEFAULT 0,
  not_before   TEXT,                 -- backoff / policy gate
  priority     INTEGER NOT NULL DEFAULT 100,
  last_error   TEXT,
  started_at   TEXT, finished_at TEXT,
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(meeting_id, stage)
);
CREATE INDEX ix_jobs_runnable ON jobs(state, not_before, priority);

CREATE TABLE detector_events (          -- observability (DETECTION.md §8)
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  process     TEXT, window_title TEXT,
  peak_score  INTEGER NOT NULL,
  evidence    TEXT NOT NULL,            -- JSON
  outcome     TEXT NOT NULL,            -- committed|near_miss|ignored|shadow
  meeting_id  TEXT
);

CREATE TABLE glossary (
  term TEXT PRIMARY KEY, kind TEXT, aliases TEXT, note TEXT, hits INTEGER DEFAULT 0
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Backing store for search. Always written; it is what the LIKE fallback searches when
-- FTS5 is unavailable, and what rebuilds transcripts_fts when it is.
CREATE TABLE transcript_turns (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  speaker    TEXT NOT NULL,
  at_ms      INTEGER NOT NULL,
  text       TEXT NOT NULL,
  UNIQUE(meeting_id, seq)
);
CREATE INDEX ix_turns_meeting ON transcript_turns(meeting_id);
