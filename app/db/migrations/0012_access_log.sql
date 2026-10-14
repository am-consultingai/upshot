-- Every request from the user's own AI tools and programs (D98): the meeting tools on
-- /mcp/ext/ and the file transcription service on /api/v1. Shown on the AI apps page.
CREATE TABLE access_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       TEXT NOT NULL,
  channel  TEXT NOT NULL,           -- mcp|api
  agent    TEXT NOT NULL DEFAULT '', -- what the caller calls itself; not proof of anything
  action   TEXT NOT NULL,           -- the tool, or the REST action
  target   TEXT NOT NULL DEFAULT '', -- a meeting, search words, a file
  outcome  TEXT NOT NULL,           -- ok|refused|error
  size     INTEGER NOT NULL DEFAULT 0 -- characters returned
);
CREATE INDEX access_log_by_at ON access_log(at);
