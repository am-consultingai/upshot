import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CalendarEvent } from "../api";
import { useI18n } from "../i18n";
import { addDays, dayKey, isHappening } from "../lib/calendar";
import { dueCounts } from "../lib/due";
import { formatClock } from "../lib/format";
import BusyButton from "./BusyButton";
import Tooltip from "./Tooltip";

/** "in 2h 33m", "in 12m", "now". */
export function untilLabel(start: string, now: number, words: { in: string; now: string }): string {
  const minutes = Math.round((new Date(start).getTime() - now) / 60_000);
  if (minutes <= 0) return words.now;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const span = hours > 0 ? (rest ? `${hours}h ${rest}m` : `${hours}h`) : `${rest}m`;
  return words.in.replace("{t}", span);
}

/**
 * The calendar's rail: what is recording, what is next, and what is still owed.
 *
 * Notion Calendar's "Upcoming in 27 min" is the model — the question a calendar is
 * opened to answer is rarely "what does Thursday look like" and nearly always "what
 * do I have to do next". Each block is drawn only when it has something to say: no
 * "Nothing recording" card, no "No upcoming events" placeholder. A rail with three
 * empty boxes is the dead gutter it replaced, with borders.
 */
export default function LibraryRail() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings() });
  const today = new Date();
  const from = dayKey(today);
  const to = dayKey(addDays(today, 8));
  const upcoming = useQuery({
    queryKey: ["calendar-events", from, to],
    queryFn: () => api.calendarEvents(from, to),
  });
  const open = useQuery({
    queryKey: ["action-items", "open"],
    queryFn: () => api.actionItems({ open: "true" }),
  });

  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const record = useMutation({
    mutationFn: (event: CalendarEvent) =>
      api.startRecording({ calendar_id: event.calendar_id, event_id: event.event_id }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const recording = status.data?.recorder.active ?? false;
  const live = (meetings.data?.meetings ?? []).find(
    (meeting) =>
      meeting.state === "RECORDING" &&
      (!status.data?.recorder.meeting_id || meeting.id === status.data.recorder.meeting_id),
  );
  const levels = status.data?.recorder.levels ?? {};
  const bothSides = "me" in levels && "them" in levels;

  /*
   * The next thing on the calendar that nobody is recording yet: not declined, not
   * all-day, not already over. One on now counts — "Record this one" matters most
   * for the meeting that has just started without you pressing anything.
   */
  const next = (upcoming.data?.events ?? [])
    .filter((event) => !event.all_day && event.response !== "declined" && !event.meeting_id)
    .filter((event) => new Date(event.end).getTime() > now)
    .filter((event) => new Date(event.start).getTime() > now || isHappening(event, new Date(now)))
    .sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime())[0];

  const counts = dueCounts(open.data?.items ?? []);
  const anyOpen = counts.overdue + counts.week + counts.later + counts.none > 0;

  return (
    <aside
      data-testid="library-rail"
      className="hidden w-66 shrink-0 flex-col gap-3.5 overflow-y-auto border-s border-line-subtle p-4 xl:flex"
    >
      {recording && (
        <section data-testid="rail-now-recording">
          <h2 className="text-2xs uppercase tracking-wide text-tertiary">{t("rail.nowRecording")}</h2>
          <div className="mt-2 rounded-lg bg-raised p-3 shadow-[var(--shadow-ring-subtle),var(--shadow-sm),var(--shadow-edge),inset_2px_0_0_0_var(--danger)]">
            {live && (
              <p className="text-2xs text-danger">
                {t("rail.minutesIn").replace(
                  "{n}",
                  String(Math.max(0, Math.round((now - new Date(live.started_at).getTime()) / 60_000))),
                )}
              </p>
            )}
            <p className="mt-1 truncate text-md font-medium tracking-snug">
              {live?.title ?? t("timeline.recording")}
            </p>
            {live && (
              <p className="mt-0.5 font-mono text-2xs text-secondary">
                {formatClock(live.started_at)}
                {bothSides && ` · ${t("rail.bothSides")}`}
              </p>
            )}
            <Tooltip label={t("rail.stopAndSummarize")} hint={t("help.stopSummarize")}>
            <BusyButton
              data-testid="rail-stop"
              busy={stop.isPending}
              onClick={() => stop.mutate()}
              className="mt-2.5 h-7 w-full justify-center rounded-md text-xs font-medium text-primary shadow-[var(--shadow-ring)] hover:bg-a-200 active:bg-a-300"
            >
              {t("rail.stopAndSummarize")}
            </BusyButton>
            </Tooltip>
          </div>
        </section>
      )}

      {next && (
        <section data-testid="rail-up-next" data-event={next.event_id}>
          <h2 className="text-2xs uppercase tracking-wide text-tertiary">{t("rail.upNext")}</h2>
          <div className="mt-2 rounded-lg bg-raised p-3 shadow-[var(--shadow-ring-subtle),var(--shadow-sm),var(--shadow-edge)]">
            <p data-testid="rail-up-next-when" className="text-2xs text-tertiary">
              {untilLabel(next.start, now, { in: t("rail.in"), now: t("rail.happeningNow") })}
            </p>
            <p className="mt-1 truncate text-md font-medium tracking-snug">
              {next.title ?? t("calendar.untitled")}
            </p>
            <p className="mt-0.5 font-mono text-2xs text-secondary">
              {formatClock(next.start)} – {formatClock(next.end)}
              {next.attendees.length > 0 &&
                ` · ${t("rail.guests").replace("{n}", String(next.attendees.length))}`}
            </p>
            {!recording && (
              <Tooltip label={t("rail.recordThisOne")} hint={t("help.recordThis")}>
              <BusyButton
                data-testid="rail-record-this"
                busy={record.isPending}
                onClick={() => record.mutate(next)}
                className="mt-2.5 h-7 w-full justify-center rounded-md bg-accent text-xs font-medium text-on-accent shadow-[var(--shadow-sm),var(--shadow-edge)] hover:brightness-110 active:translate-y-px"
              >
                {t("rail.recordThisOne")}
              </BusyButton>
              </Tooltip>
            )}
          </div>
        </section>
      )}

      {anyOpen && (
        <section data-testid="rail-open-items">
          <Tooltip label={t("rail.openItems")} hint={t("help.openItems")}>
            <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("rail.openItems")}</h2>
          </Tooltip>
          <ul className="mt-2 flex flex-col gap-0.5">
            {(
              [
                ["overdue", t("rail.overdue"), "text-danger", "text-secondary"],
                ["week", t("rail.dueThisWeek"), "text-secondary", "text-secondary"],
                ["later", t("rail.later"), "text-secondary", "text-secondary"],
                ["none", t("rail.noDate"), "text-tertiary", "text-tertiary"],
              ] as const
            )
              .filter(([key]) => counts[key] > 0)
              .map(([key, label, number, text]) => (
                <li key={key}>
                  <Link
                    to={`/actions#${key}`}
                    data-testid="rail-open-count"
                    data-bucket={key}
                    className="-mx-1.5 flex items-baseline gap-2 rounded-sm px-1.5 py-1 hover:bg-a-200"
                  >
                    <span className={`font-mono text-2xs tabular-nums ${number}`}>{counts[key]}</span>
                    <span className={`text-sm ${text}`}>{label}</span>
                  </Link>
                </li>
              ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
