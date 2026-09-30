-- What the user writes about a meeting when they start recording it, or later: a
-- description, and when it is planned to start and end (ISO 8601, local offset). All
-- optional. The recording's own start and end stay in started_at / ended_at.
ALTER TABLE meetings ADD COLUMN description TEXT;
ALTER TABLE meetings ADD COLUMN planned_start TEXT;
ALTER TABLE meetings ADD COLUMN planned_end TEXT;
