-- Recordings merged into another (D89): the id of the one that went keeps resolving to the
-- one it went into, so a link, a notification or an open page still finds the meeting.
CREATE TABLE meeting_aliases (
  alias       TEXT PRIMARY KEY,
  meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL
);
CREATE INDEX meeting_aliases_by_meeting ON meeting_aliases(meeting_id);
