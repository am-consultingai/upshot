-- Several Google accounts at once (epic z8tj1hb9je, D82). Every event and every matched
-- recording carries the account it came from.
--
-- An account is never deleted: removing it sets removed_at, which hides everything of it
-- and keeps the row, the address with it, so connecting the same address again restores
-- the same id and its history. The address is kept here, not only beside the token
-- (D43): a removed account's token is gone and its history must still find its way back.
CREATE TABLE calendar_accounts (
  id          TEXT PRIMARY KEY,               -- 'ga_' + 8 hex; opaque
  address     TEXT NOT NULL UNIQUE COLLATE NOCASE,
  visible     INTEGER NOT NULL DEFAULT 1,     -- the hide switch: off hides it everywhere
  removed_at  TEXT,                           -- permanent hide
  color       INTEGER NOT NULL,               -- 1..6, the dot
  position    INTEGER NOT NULL,
  added_at    TEXT NOT NULL
);

-- The calendars read from each account. One 'primary' row per account until every
-- calendar of an account can be read (epic z8tj1h9c03).
CREATE TABLE calendar_sources (
  account_id  TEXT NOT NULL REFERENCES calendar_accounts(id),
  calendar_id TEXT NOT NULL,
  title       TEXT,
  visible     INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (account_id, calendar_id)
);

-- Every account the matched event of a meeting appears on: an invitation that reached
-- both a work and a personal calendar is one meeting that belongs to both. A meeting is
-- hidden only when every account it belongs to is hidden; one with no rows is always shown.
CREATE TABLE meeting_calendar_accounts (
  meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  account_id  TEXT NOT NULL,
  PRIMARY KEY (meeting_id, account_id)
);
CREATE INDEX ix_meeting_calendar_accounts_account ON meeting_calendar_accounts(account_id);

-- The account of the copy the meeting was matched to: whose token reads its invitation.
ALTER TABLE meetings ADD COLUMN calendar_account_id TEXT;
CREATE INDEX ix_meetings_calendar_account ON meetings(calendar_account_id);

-- The event cache, rebuilt with its rows (they take their account at start-up, from the
-- one account an older install had). Sync no longer deletes an event Google stops
-- returning: it sets removed_at, and every reader skips those.
CREATE TABLE calendar_events_new (
  account_id         TEXT NOT NULL DEFAULT '',
  calendar_id        TEXT NOT NULL,
  event_id           TEXT NOT NULL,
  ical_uid           TEXT,
  recurring_event_id TEXT,
  original_start     TEXT,
  title              TEXT,
  start_at           TEXT NOT NULL,
  end_at             TEXT NOT NULL,
  all_day            INTEGER NOT NULL DEFAULT 0,
  time_zone          TEXT,
  status             TEXT,
  response           TEXT,
  transparent        INTEGER NOT NULL DEFAULT 0,
  event_type         TEXT,
  visibility         TEXT,
  attendees_json     TEXT,
  attendee_count     INTEGER NOT NULL DEFAULT 0,
  attendees_omitted  INTEGER NOT NULL DEFAULT 0,
  conference_url     TEXT,
  updated            TEXT,
  synced_at          TEXT NOT NULL,
  removed_at         TEXT,
  PRIMARY KEY (account_id, calendar_id, event_id)
);
INSERT INTO calendar_events_new (
  account_id, calendar_id, event_id, ical_uid, recurring_event_id, original_start, title,
  start_at, end_at, all_day, time_zone, status, response, transparent, event_type,
  visibility, attendees_json, attendee_count, attendees_omitted, conference_url, updated,
  synced_at
)
SELECT
  '', calendar_id, event_id, ical_uid, recurring_event_id, original_start, title,
  start_at, end_at, all_day, time_zone, status, response, transparent, event_type,
  visibility, attendees_json, attendee_count, attendees_omitted, conference_url, updated,
  synced_at
FROM calendar_events;
DROP TABLE calendar_events;
ALTER TABLE calendar_events_new RENAME TO calendar_events;
CREATE INDEX ix_calendar_events_start ON calendar_events(start_at);
CREATE INDEX ix_calendar_events_end ON calendar_events(end_at);
CREATE INDEX ix_calendar_events_account ON calendar_events(account_id);
