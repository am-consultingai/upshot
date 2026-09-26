import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type CalendarEvent, type Meeting } from "../api";
import { useI18n } from "../i18n";
import {
  allDayKeys,
  asGridItem,
  bucketByDay,
  dayKey,
  eventKey,
  isToday,
  placement,
  recordedIds,
  startOfDay,
  timedEvents,
  workingHours,
  zoneLabel,
} from "../lib/calendar";
import { formatClock } from "../lib/format";
import { timelineLayout } from "../lib/timeline";
import Tooltip from "./Tooltip";

/** 48px an hour: a 30-minute meeting holds a title and a time, and a working day fits. */
const PX_PER_MINUTE = 0.8;
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);
/** The hour gutter. */
const GUTTER = 48;
/** Under this a chip shows its title alone, on one line — Amie's rule, at 48px an hour. */
const SHORT_MINUTES = 40;

/**
 * Where the grid opens when nothing says otherwise: the start of the default working
 * day. The week used to open at 00:00 every time, so the entire working day was below
 * the fold and the first act on every visit was scrolling past nine empty hours. A
 * calendar that does not open on your day is not a calendar you use twice. The user's
 * own working hours (Settings, Calendar) replace it.
 */
const DEFAULT_HOUR = 8;
/** A little air above the first meeting, so it does not sit flush against the header. */
const LEAD_MINUTES = 30;

/** Minutes past midnight of the earliest thing drawn, across every day shown. */
export function firstMinute(
  starts: string[],
  fallbackHour: number = DEFAULT_HOUR,
): number {
  let earliest: number | null = null;
  for (const start of starts) {
    const at = new Date(start);
    if (Number.isNaN(at.getTime())) continue;
    const minute = at.getHours() * 60 + at.getMinutes();
    if (earliest === null || minute < earliest) earliest = minute;
  }
  if (earliest === null) return fallbackHour * 60;
  return Math.max(0, earliest - LEAD_MINUTES);
}

/** Minutes past midnight, which is the unit the grid is laid out in. */
function minutesIntoDay(at: Date): number {
  return at.getHours() * 60 + at.getMinutes();
}

/** What a block on the grid is, which decides its colour and its badge. */
export type ChipKind = "recorded" | "scheduled" | "live" | "failed";

/**
 * The four kinds of block.
 *
 * Colour is spent on state and on nothing else: accent for a recording, grey for an
 * event nobody recorded, red for one that is recording now or failed. The source is
 * the badge's job — Upshot's mark, a calendar glyph, a pulse, a "!" — so the two
 * questions a block answers never compete for the same channel.
 */
const CHIP: Record<ChipKind, string> = {
  recorded:
    "bg-accent-quiet shadow-[inset_2px_0_0_0_var(--accent)] hover:bg-[color-mix(in_oklab,var(--base),var(--accent)_18%)] hover:shadow-[inset_2px_0_0_0_var(--accent),0_1px_3px_rgb(0_0_0/10%)]",
  scheduled:
    "bg-surface-2 shadow-[inset_2px_0_0_0_var(--border-strong)] hover:bg-surface-3 hover:shadow-[inset_2px_0_0_0_var(--border-strong),0_1px_3px_rgb(0_0_0/10%)]",
  live: "bg-[color-mix(in_oklab,var(--base),var(--danger)_12%)] shadow-[inset_2px_0_0_0_var(--danger)] hover:bg-[color-mix(in_oklab,var(--base),var(--danger)_18%)]",
  failed:
    "bg-danger-quiet shadow-[inset_2px_0_0_0_var(--danger)] hover:bg-[color-mix(in_oklab,var(--base),var(--danger)_14%)] hover:shadow-[inset_2px_0_0_0_var(--danger),0_1px_3px_rgb(0_0_0/10%)]",
};

/**
 * What a recorded meeting still owes, as a balloon on its chip.
 *
 * The sidebar row says "3 items"; the calendar said nothing, so a week of meetings read
 * as a week of finished business. The count is the open ones only — a meeting whose
 * commitments are all done has nothing to flag.
 */
export function OpenBalloon({ count, label }: { count: number | undefined; label: string }) {
  const { t } = useI18n();
  if (!count) return null;
  return (
    <Tooltip label={label.replace("{n}", String(count))} hint={t("help.balloon")}>
    <span
      data-testid="chip-open"
      data-count={count}
      aria-label={label.replace("{n}", String(count))}
      className="grid h-4 min-w-4 shrink-0 place-items-center self-start rounded-full bg-primary px-1 font-mono text-[10px] font-semibold leading-none text-canvas tabular-nums"
    >
      {count}
    </span>
    </Tooltip>
  );
}

function Badge({ kind }: { kind: ChipKind }) {
  if (kind === "live") {
    return (
      <span
        aria-hidden="true"
        className="grid size-[15px] shrink-0 place-items-center rounded-[4px] bg-danger"
      >
        <span className="ma-pulse size-1.5 rounded-full bg-on-solid" />
      </span>
    );
  }
  return (
    <span
      data-testid={kind === "scheduled" ? "calendar-event-flag" : "calendar-recorded-flag"}
      aria-hidden="true"
      className={`grid size-[15px] shrink-0 place-items-center rounded-[4px] bg-raised shadow-[var(--shadow-ring-subtle)] ${
        kind === "failed" ? "text-danger" : "text-secondary"
      }`}
    >
      <svg viewBox="0 0 16 16" className="size-[9px] fill-none stroke-current" strokeLinecap="round">
        {kind === "scheduled" ? (
          <g strokeWidth={1.8}>
            <rect x="2.5" y="3.5" width="11" height="10" rx="1.5" />
            <path d="M2.5 6.5h11M5.5 2v2M10.5 2v2" />
          </g>
        ) : kind === "failed" ? (
          <path d="M8 4v5M8 11.5v.5" strokeWidth={2.2} />
        ) : (
          <path d="M2 9.5 5 5l3 4 3-6 3 6.5" strokeWidth={2} />
        )}
      </svg>
    </span>
  );
}

/** `16:30 – 17:30`, or just the start when the end is not known. */
export function timeRange(start: string, minutes: number | null): string {
  if (!minutes || minutes < 1) return formatClock(start);
  const end = new Date(new Date(start).getTime() + minutes * 60_000).toISOString();
  return `${formatClock(start)} – ${formatClock(end)}`;
}

/** What a chip's second line says: the range, or what is wrong, or how long it has run. */
export function chipWhen(
  kind: ChipKind,
  start: string,
  minutes: number | null,
  words: { recording: string; failed: string },
  now: number = Date.now(),
): string {
  if (kind === "live") {
    const ran = Math.max(0, Math.round((now - new Date(start).getTime()) / 60_000));
    return `${words.recording} · ${ran}m`;
  }
  if (kind === "failed") return `${formatClock(start)} · ${words.failed}`;
  return timeRange(start, minutes);
}

export function kindOf(meeting: Pick<Meeting, "state"> | undefined): ChipKind {
  if (!meeting) return "recorded";
  if (meeting.state === "RECORDING") return "live";
  if (meeting.state === "FAILED") return "failed";
  return "recorded";
}

/**
 * The day and week views: hour rows, one column per day, each meeting a block at its
 * real start time with its real duration. Overlapping meetings are packed into columns
 * by `timelineLayout`, the same function the list view uses.
 */
export default function TimeGrid({
  days,
  meetings,
  events = [],
  onEvent,
}: {
  days: Date[];
  meetings: Meeting[];
  /** Google Calendar events behind the recordings. A matched pair draws as one block. */
  events?: CalendarEvent[];
  onEvent?: (event: CalendarEvent) => void;
}) {
  const { t, locale } = useI18n();
  // The event keeps its name and its slot; a recording of it only marks the block. A
  // recording with no event of its own still draws as itself.
  const recorded = recordedIds(events);
  const byId = new Map(meetings.map((meeting) => [meeting.id, meeting]));
  const buckets = bucketByDay(meetings.filter((meeting) => !recorded.has(meeting.id)));
  const shown = timedEvents(events);
  const eventBuckets = bucketByDay(shown.map((event) => ({ ...event, started_at: event.start })));
  const allDay = events.filter((item) => item.all_day);
  const tinted = new Set(allDay.flatMap((event) => allDayKeys(event)));
  const dow = new Intl.DateTimeFormat(locale, { weekday: "short" });
  const words = { recording: t("timeline.recordingShort"), failed: t("timeline.failedShort") };

  /*
   * Where "now" sits, in minutes past midnight, ticking once a minute.
   *
   * Only computed when the grid actually contains today — a week in March has no
   * now-line to draw, and a timer that fires for it is a timer that wakes the tab
   * for nothing.
   */
  const showsToday = days.some(isToday);
  const [nowMinute, setNowMinute] = useState<number | null>(() =>
    showsToday ? minutesIntoDay(new Date()) : null,
  );
  useEffect(() => {
    if (!showsToday) {
      setNowMinute(null);
      return undefined;
    }
    setNowMinute(minutesIntoDay(new Date()));
    const timer = window.setInterval(() => setNowMinute(minutesIntoDay(new Date())), 60_000);
    return () => window.clearInterval(timer);
  }, [showsToday]);
  const nowLabel = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
  const todayStart = startOfDay(new Date()).getTime();

  /*
   * Open on the day, not on midnight.
   *
   * Scrolled rather than clipped: the small hours still exist and can be scrolled
   * back to, which matters for a meeting that genuinely ran at 07:00 or 22:00. The
   * key is the set of days, so moving to another week re-aims at that week's first
   * meeting instead of keeping the previous one's scroll.
   */
  const scroller = useRef<HTMLDivElement | null>(null);
  const spanKey = days.map(dayKey).join(",");
  // The working day decides where the grid opens and what it shades; a meeting before
  // the day starts pulls the opening earlier so it is not hidden above the fold.
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const work = workingHours(settings.data?.config);
  const openAt = Math.min(
    firstMinute(
      [...meetings.map((meeting) => meeting.started_at), ...shown.map((event) => event.start)],
      work.start,
    ),
    Math.max(0, work.start * 60 - LEAD_MINUTES),
  );
  /*
   * Aimed once per period, and only once the period has something in it.
   *
   * Both the meetings and the events arrive asynchronously, so the first render of a
   * week is always empty — computing the scroll position then pinned it to the 08:00
   * fallback and never corrected itself. Waiting for the first non-empty render fixes
   * it; keying the guard on the period rather than on the position means scrolling by
   * hand afterwards is not yanked back, and moving to another week aims again.
   */
  const aimedAt = useRef<string | null>(null);
  const empty = meetings.length === 0 && shown.length === 0;
  useEffect(() => {
    const node = scroller.current;
    if (!node || aimedAt.current === spanKey) return;
    node.scrollTop = openAt * PX_PER_MINUTE;
    if (!empty && settings.isSuccess) aimedAt.current = spanKey;
  }, [spanKey, openAt, empty, settings.isSuccess]);

  const columns = `${GUTTER}px repeat(${days.length}, minmax(6rem, 1fr))`;
  const first = days[0];
  const lastKey = dayKey(days[days.length - 1]);

  /** One block, whatever it came from. */
  const chip = (input: {
    key: string;
    kind: ChipKind;
    title: string;
    start: string;
    /** How tall it is drawn. */
    minutes: number;
    /** How long it really was, for the range it prints; null when not known. */
    length: number | null;
    startMinute: number;
    slot: { column: number; columns: number };
    past: boolean;
    /** Open action items, for the balloon. */
    open?: number;
  }) => {
    const short = input.minutes < SHORT_MINUTES;
    return {
      className: `absolute flex gap-[5px] overflow-hidden rounded-xs text-start ${CHIP[input.kind]} ${
        short ? "items-center px-[5px] py-px" : "px-[5px] py-[3px]"
      } ${input.past && input.kind !== "live" ? "opacity-[.82]" : ""}`,
      style: {
        top: input.startMinute * PX_PER_MINUTE,
        height: Math.max(16, input.minutes * PX_PER_MINUTE),
        // Logical properties: the columns mirror under RTL for free. 3px/4px of air
        // either side, so two blocks in one hour do not read as one.
        insetInlineStart: `calc(${(input.slot.column / input.slot.columns) * 100}% + 3px)`,
        width: `calc(${(1 / input.slot.columns) * 100}% - 7px)`,
      },
      body: (
        <>
          <Badge kind={input.kind} />
          <span className="min-w-0 flex-1 leading-tight">
            <span
              className={`block truncate text-xs ${
                input.kind === "scheduled"
                  ? "font-normal text-secondary"
                  : input.kind === "live"
                    ? "font-semibold text-danger"
                    : "font-medium text-primary"
              }`}
            >
              {input.title}
            </span>
            {!short && (
              <span
                data-testid="chip-when"
                className="mt-px block truncate font-mono text-[10px] text-secondary opacity-75"
              >
                {chipWhen(input.kind, input.start, input.length, words)}
              </span>
            )}
          </span>
          <OpenBalloon count={input.open} label={t("timeline.actionsOpen")} />
        </>
      ),
    };
  };

  return (
    <div
      ref={scroller}
      data-testid="calendar-timegrid"
      data-open-minute={openAt}
      className="h-full overflow-auto"
    >
      <div style={{ minWidth: GUTTER + days.length * 96 }}>
        {/*
         * The header and the all-day band are pinned together, so scrolling to the
         * working day no longer takes the day names with it.
         */}
        <div className="sticky top-0 z-20 bg-canvas">
          <div className="grid border-b border-line-subtle" style={{ gridTemplateColumns: columns }}>
            <div
              data-testid="calendar-zone"
              className="grid place-items-center font-mono text-[10px] text-tertiary"
            >
              {zoneLabel(new Date(), locale)}
            </div>
            {days.map((day) => {
              const today = isToday(day);
              const past = day.getTime() < todayStart;
              return (
                <div
                  key={dayKey(day)}
                  data-testid="calendar-daycolumn"
                  data-day={dayKey(day)}
                  data-today={today ? "true" : undefined}
                  className="pt-2 pb-[7px] text-center"
                >
                  <div
                    className="text-2xs uppercase tracking-wide text-tertiary"
                  >
                    {dow.format(day)}
                  </div>
                  <div
                    className={`mt-0.5 text-lg leading-[1.1] font-medium tracking-snug tabular-nums ${
                      today
                        ? "inline-grid h-6.5 min-w-6.5 place-items-center rounded-sm bg-accent px-1.5 font-semibold text-on-accent"
                        : past
                          ? "text-tertiary"
                          : "text-primary"
                    }`}
                  >
                    {day.getDate()}
                  </div>
                </div>
              );
            })}
          </div>

          {/*
           * All-day gets its own band instead of crowding the date, and an event that
           * runs several days is one chip across them rather than a copy in each.
           */}
          <div data-testid="calendar-allday-band" className="flex min-h-6.5 border-b border-line-subtle">
            <div
              className="grid shrink-0 place-items-center text-[10px] text-tertiary"
              style={{ width: GUTTER }}
            >
              {t("timeline.allDay")}
            </div>
            <div
              className="grid flex-1 gap-y-0.5 py-[3px]"
              style={{
                gridTemplateColumns: `repeat(${days.length}, minmax(6rem, 1fr))`,
                gridAutoFlow: "row dense",
              }}
            >
              {allDay.map((event) => {
                const keys = allDayKeys(event).filter((key) => key >= dayKey(first) && key <= lastKey);
                if (keys.length === 0) return null;
                const from = days.findIndex((day) => dayKey(day) === keys[0]);
                return (
                  <button
                    key={eventKey(event)}
                    type="button"
                    data-testid="calendar-allday"
                    data-event={event.event_id}
                    onClick={() => onEvent?.(event)}
                    title={event.title ?? t("calendar.untitled")}
                    className="mx-[3px] block truncate rounded-xs bg-warning-quiet px-1.5 py-0.5 text-start text-2xs text-warning hover:brightness-95"
                    style={{ gridColumn: `${from + 1} / span ${keys.length}` }}
                  >
                    {event.title ?? t("calendar.untitled")}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="grid" style={{ gridTemplateColumns: columns }}>
          <div className="relative" style={{ height: 24 * 60 * PX_PER_MINUTE }}>
            {HOURS.map((hour) =>
              hour === 0 ? null : (
                <div
                  key={hour}
                  className={`absolute -translate-y-1/2 font-mono text-2xs tabular-nums ${
                    nowMinute !== null && Math.abs(hour * 60 - nowMinute) < 20
                      ? "opacity-0"
                      : "text-tertiary"
                  }`}
                  style={{ top: hour * 60 * PX_PER_MINUTE, insetInlineEnd: "0.5rem" }}
                >
                  {String(hour).padStart(2, "0")}:00
                </div>
              ),
            )}
            {nowMinute !== null && (
              <span
                data-testid="now-time"
                className="absolute z-10 -translate-y-1/2 font-mono text-2xs font-medium tabular-nums text-accent"
                style={{ top: nowMinute * PX_PER_MINUTE, insetInlineEnd: "0.5rem" }}
              >
                {nowLabel}
              </span>
            )}
          </div>

          {days.map((day) => {
            const items = buckets.get(dayKey(day)) ?? [];
            const dayEvents = eventBuckets.get(dayKey(day)) ?? [];
            // One layout for both, so an event and an unrelated recording at the same hour
            // sit side by side instead of on top of each other.
            const placed = timelineLayout([...items, ...dayEvents.map(asGridItem)]);
            return (
              <div
                key={dayKey(day)}
                data-testid="calendar-daybody"
                data-day={dayKey(day)}
                data-tinted={tinted.has(dayKey(day)) ? "true" : undefined}
                className={`relative border-s border-line-subtle ${
                  // An all-day event tints its whole column — Amie's move — so a day
                  // someone is away reads as different before any chip is read.
                  tinted.has(dayKey(day))
                    ? "bg-[color-mix(in_oklab,var(--surface-0),var(--warning)_3%)]"
                    : ""
                }`}
                style={{ height: 24 * 60 * PX_PER_MINUTE }}
              >
                {/* Outside working hours: shaded, still there to scroll to. */}
                {work.start > 0 && (
                  <div
                    data-testid="off-hours"
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-x-0 top-0 bg-a-100"
                    style={{ height: work.start * 60 * PX_PER_MINUTE }}
                  />
                )}
                {work.end < 24 && (
                  <div
                    data-testid="off-hours"
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-x-0 bottom-0 bg-a-100"
                    style={{ top: work.end * 60 * PX_PER_MINUTE }}
                  />
                )}
                {HOURS.map((hour) => (
                  <div
                    key={hour}
                    className="absolute w-full border-t border-line-subtle"
                    style={{ top: hour * 60 * PX_PER_MINUTE }}
                  />
                ))}
                {isToday(day) && nowMinute !== null && (
                  <div
                    data-testid="now-line"
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-x-0 z-10 h-px bg-accent"
                    style={{ top: nowMinute * PX_PER_MINUTE }}
                  >
                    <span
                      className="absolute -top-[3px] size-[7px] rounded-full bg-accent"
                      style={{ insetInlineStart: -3 }}
                    />
                  </div>
                )}
                {placed.map((slot) => {
                  const event = dayEvents.find((item) => eventKey(item) === slot.id);
                  if (event) {
                    const { startMinute, minutes } = placement(
                      { started_at: event.start, duration_s: asGridItem(event).duration_s },
                      day,
                    );
                    const past = new Date(event.end).getTime() < Date.now();
                    const wasRecorded = Boolean(event.meeting_id);
                    const kind = wasRecorded
                      ? kindOf(byId.get(event.meeting_id as string))
                      : "scheduled";
                    const built = chip({
                      key: slot.id,
                      kind,
                      title: event.title ?? t("calendar.untitled"),
                      start: event.start,
                      minutes,
                      length: minutes,
                      startMinute,
                      slot,
                      past,
                      open: wasRecorded ? byId.get(event.meeting_id as string)?.actions_open : undefined,
                    });
                    if (wasRecorded) {
                      return (
                        <Link
                          key={slot.id}
                          to={`/m/${event.meeting_id}`}
                          data-testid="calendar-gevent"
                          data-event={event.event_id}
                          data-recorded="true"
                          data-kind={kind}
                          data-meeting={event.meeting_id}
                          title={`${event.title ?? ""} — ${t("calendar.recorded")}`}
                          className={built.className}
                          style={built.style}
                        >
                          {built.body}
                        </Link>
                      );
                    }
                    return (
                      <button
                        key={slot.id}
                        type="button"
                        data-testid="calendar-gevent"
                        data-event={event.event_id}
                        data-recorded="false"
                        data-kind={kind}
                        data-past={past}
                        title={event.title ?? ""}
                        onClick={() => onEvent?.(event)}
                        className={built.className}
                        style={built.style}
                      >
                        {built.body}
                      </button>
                    );
                  }
                  const meeting = items.find((item) => item.id === slot.id);
                  if (!meeting) return null;
                  const { startMinute, minutes } = placement(meeting, day);
                  const kind = kindOf(meeting);
                  const end = new Date(meeting.started_at).getTime() + minutes * 60_000;
                  // A recording still running has no length yet; it is drawn to now.
                  const ran = (Date.now() - new Date(meeting.started_at).getTime()) / 60_000;
                  const built = chip({
                    key: meeting.id,
                    kind,
                    title: meeting.title ?? t("timeline.recording"),
                    start: meeting.started_at,
                    minutes: kind === "live" ? Math.max(minutes, ran) : minutes,
                    length: meeting.duration_s ? minutes : null,
                    startMinute,
                    slot,
                    past: end < Date.now(),
                    open: meeting.actions_open,
                  });
                  return (
                    <Link
                      key={meeting.id}
                      to={`/m/${meeting.id}`}
                      data-testid="calendar-event"
                      data-meeting={meeting.id}
                      data-kind={kind}
                      title={meeting.title ?? meeting.id}
                      className={built.className}
                      style={built.style}
                    >
                      {built.body}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
