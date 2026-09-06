-- NEEDS_REVIEW is gone. It was set by sanity gates that read the structured notes, and
-- those went with the structured summariser: nothing produces the flag any more, and a
-- meeting left carrying it just shows an amber badge nobody can clear or explain.
--
-- Rows are moved to the furthest state their artifacts justify. The flag was sticky, so
-- it masked whatever the pipeline had actually reached; deliver having run is the best
-- evidence available from the jobs table.
UPDATE meetings SET state = (
  SELECT CASE
    WHEN EXISTS (SELECT 1 FROM jobs j WHERE j.meeting_id = meetings.id
                 AND j.stage = 'deliver' AND j.state = 'done') THEN 'DELIVERED'
    WHEN EXISTS (SELECT 1 FROM jobs j WHERE j.meeting_id = meetings.id
                 AND j.stage = 'render' AND j.state = 'done') THEN 'RENDERED'
    WHEN EXISTS (SELECT 1 FROM jobs j WHERE j.meeting_id = meetings.id
                 AND j.stage = 'summarize' AND j.state = 'done') THEN 'SUMMARIZED'
    ELSE 'TRANSCRIBED'
  END
) WHERE state = 'NEEDS_REVIEW';
