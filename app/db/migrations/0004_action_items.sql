-- The one piece of structure the free-form summariser still hands back.
--
-- D46 removed NOTES_SCHEMA because a nine-field shape meant an edited prompt could
-- change wording but never structure. This table does not bring that back: the model
-- still writes whatever document it likes into summary_html. It is now also asked to
-- hand back the action items it just wrote, so the application can read across
-- meetings — an action-item inbox, per-person views, "what did I promise this week"
-- — which known-issues #10 recorded as impossible against opaque HTML.
--
-- `done_at` is the user's, not the model's. Re-summarizing replaces every row for a
-- meeting, so the replace path carries the done state forward by matching on the
-- normalised text; losing a tick because the prompt was edited would make the
-- checkbox untrustworthy, and an untrustworthy checkbox is worse than none.
CREATE TABLE action_items (
  id          INTEGER PRIMARY KEY,
  meeting_id  TEXT    NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,          -- the order the model listed them in
  who         TEXT    NOT NULL,          -- a name, or ME/THEM as the prompt allows
  what        TEXT    NOT NULL,
  norm        TEXT    NOT NULL,          -- `what`, casefolded and squeezed: the match key
  due         TEXT,                      -- free text; the model is not asked to parse dates
  at_ms       INTEGER,                   -- the moment it was agreed, when the model knows
  mine        INTEGER NOT NULL DEFAULT 0,-- owner resolved to the person running this recorder
  done_at     TEXT,                      -- NULL while open
  created_at  TEXT    NOT NULL,
  UNIQUE (meeting_id, seq)
);

CREATE INDEX ix_action_items_meeting ON action_items(meeting_id);
-- The inbox's own query: everything still open, mine first, newest meeting first.
CREATE INDEX ix_action_items_open    ON action_items(done_at, mine);
CREATE INDEX ix_action_items_norm    ON action_items(meeting_id, norm);
