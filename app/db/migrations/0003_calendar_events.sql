-- Calendar 2 (z8tj1h8jrh): a cache of Google Calendar events, and nothing more. It is
-- safe to delete at any time: the next sync rebuilds it. Disconnecting deletes it.
--
-- What is deliberately absent (Calendar 7, z8tj1h8jrp): the description, which routinely
-- carries dial-in PINs and forwarded mail, and attendee email addresses. Attendees are
-- kept as display names only.
--
-- Times are UTC instants ("...Z"), so ordering and overlap tests are plain string
-- comparisons and a DST change cannot move an event. An all-day event keeps its dates
-- as midnight UTC of its (exclusive-end) date range and is flagged; it is shown, never
-- matched to a recording.
CREATE TABLE calendar_events (
  calendar_id        TEXT NOT NULL,
  event_id           TEXT NOT NULL,     -- unique only within its calendar
  ical_uid           TEXT,              -- shared by every instance of a series
  recurring_event_id TEXT,
  original_start     TEXT,              -- with recurring_event_id: one occurrence, stably
  title              TEXT,
  start_at           TEXT NOT NULL,
  end_at             TEXT NOT NULL,
  all_day            INTEGER NOT NULL DEFAULT 0,
  time_zone          TEXT,
  status             TEXT,              -- confirmed|tentative
  response           TEXT,              -- this user's answer: accepted|declined|tentative|needsAction
  transparent        INTEGER NOT NULL DEFAULT 0,   -- "show me as available": a soft hold
  event_type         TEXT,
  visibility         TEXT,              -- default|public|private|confidential
  attendees_json     TEXT,              -- [{"name": ..., "optional": bool, "self": bool}]
  attendee_count     INTEGER NOT NULL DEFAULT 0,   -- people, excluding rooms
  attendees_omitted  INTEGER NOT NULL DEFAULT 0,
  conference_url     TEXT,
  updated            TEXT,
  synced_at          TEXT NOT NULL,
  PRIMARY KEY (calendar_id, event_id)
);
CREATE INDEX ix_calendar_events_start ON calendar_events(start_at);
CREATE INDEX ix_calendar_events_end   ON calendar_events(end_at);
