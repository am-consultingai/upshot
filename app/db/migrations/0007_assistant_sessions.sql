-- The assistant's conversations (D61, D62, assistant plan step 4).
--
-- A session is one conversation in the panel. `cli_session_id` is the CLI's own session,
-- passed back with --resume so the model keeps its context between questions; it can go
-- missing (the CLI keeps its sessions in the user's profile, not here), in which case
-- the next question starts a new CLI session with a recap of this one.
--
-- A message is stored in the AI SDK's UIMessage shape — `parts_json` is its `parts`
-- array — so a conversation reopens exactly as it was shown: text, tool steps, citations.
CREATE TABLE assistant_sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '',
  cli_session_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE assistant_messages (
  id TEXT NOT NULL,
  session_id TEXT NOT NULL REFERENCES assistant_sessions(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  parts_json TEXT NOT NULL,
  -- The answer's own facts: which provider and model wrote it.
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  PRIMARY KEY (session_id, id)
);

CREATE INDEX ix_assistant_sessions_updated ON assistant_sessions(updated_at);
