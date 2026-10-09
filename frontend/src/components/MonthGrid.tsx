import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CalendarEvent, Meeting } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import {
  dayItems,
  dayKey,
  isSameMonth,
  isToday,
  weekdayLabels,
  type DayItem,
  type DotState,
} from "../lib/calendar";
import { formatClock } from "../lib/format";

/** Beyond this a cell's dots stop being countable at a glance; the rest are "+N". */
export const MAX_DOTS = 8;

/**
 * A dot's colour is its state, the same channel the week view spends on it: accent for a
 * recording, red for one that failed or is recording now (pulsing, unless motion is
 * reduced), the warning colour for one still asking which meeting it was, grey for an
 * event nobody recorded.
 */
const DOT: Record<DotState, string> = {
  recorded: "bg-accent",
  failed: "bg-danger",
  live: "bg-danger ma-pulse ma-dot-live",
  needs: "bg-warning",
  scheduled: "bg-line",
};

const STATE_LABEL: Record<DotState, MessageKey> = {
  recorded: "calendar.stateRecorded",
  failed: "calendar.stateFailed",
  live: "calendar.stateLive",
  needs: "calendar.needsMeeting",
  scheduled: "calendar.stateScheduled",
};

/** The day's list, shown while a cell is hovered or focused. */
function DayPeek({
  id,
  anchor,
  items,
}: {
  id: string;
  anchor: DOMRect;
  items: DayItem[];
}) {
  const { t } = useI18n();
  const bubble = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const node = bubble.current;
    if (!node) return;
    const { offsetWidth: width, offsetHeight: height } = node;
    let left = anchor.left + anchor.width / 2 - width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    let top = anchor.bottom + 6;
    if (top + height > window.innerHeight - 8) top = Math.max(8, anchor.top - height - 6);
    setPlace({ left, top });
  }, [anchor]);
  return createPortal(
    <div
      ref={bubble}
      id={id}
      role="tooltip"
      data-testid="calendar-daypeek"
      className="pointer-events-none fixed z-[90] w-64 rounded-md bg-raised p-2 text-xs shadow-[var(--shadow-ring),var(--shadow-md),var(--shadow-edge)]"
      style={{ left: place?.left ?? -9999, top: place?.top ?? -9999 }}
    >
      {items.length === 0 ? (
        <p className="text-tertiary">{t("calendar.dayEmpty")}</p>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li
              key={item.key}
              data-testid="calendar-daypeek-item"
              data-state={item.state}
              className="flex items-baseline gap-1.5"
            >
              <span aria-hidden="true" className={`size-1.5 shrink-0 self-center rounded-full ${DOT[item.state]}`} />
              <bdi className="w-11 shrink-0 font-mono text-3xs text-tertiary tabular-nums">
                {item.allDay ? t("timeline.allDay") : formatClock(item.start)}
              </bdi>
              <span className="min-w-0 flex-1 truncate text-primary">
                {item.title ?? (item.key.startsWith("meeting:") ? t("timeline.recording") : t("calendar.untitled"))}
              </span>
              <span className="shrink-0 text-3xs text-secondary">{t(STATE_LABEL[item.state])}</span>
            </li>
          ))}
        </ul>
      )}
    </div>,
    document.body,
  );
}

/** One day of the month: its number and a dot per thing on it. Opens the day. */
function DayCell({
  day,
  items,
  outside,
  onDay,
}: {
  day: Date;
  items: DayItem[];
  outside: boolean;
  onDay?: (day: Date) => void;
}) {
  const { t, locale } = useI18n();
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const peekId = useId();
  const extra = Math.max(0, items.length - MAX_DOTS);
  const date = new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(day);
  const count =
    items.length === 0
      ? t("calendar.dayEmpty")
      : items.length === 1
        ? t("calendar.dayItemsOne")
        : t("calendar.dayItemsMany").replace("{n}", String(items.length));
  const show = (target: HTMLElement) => setAnchor(target.getBoundingClientRect());
  const hide = () => setAnchor(null);

  return (
    <button
      type="button"
      data-testid="calendar-daycell"
      data-day={dayKey(day)}
      data-count={items.length}
      data-today={isToday(day) ? "true" : undefined}
      aria-label={`${date}, ${count}`}
      aria-describedby={anchor && items.length > 0 ? peekId : undefined}
      title={t("calendar.openDay").replace("{date}", date)}
      onClick={() => onDay?.(day)}
      onMouseEnter={(event) => show(event.currentTarget)}
      onMouseLeave={hide}
      onFocus={(event) => show(event.currentTarget)}
      onBlur={hide}
      className={`flex min-h-24 flex-col items-start rounded-lg p-1.5 text-start outline-none focus-visible:shadow-[inset_0_0_0_2px_var(--accent)] ${
        outside ? "text-tertiary opacity-50 hover:bg-a-100" : "bg-surface-1 hover:bg-surface-2"
      }`}
    >
      <span
        className={`mb-1 text-xs ${
          isToday(day) ? "inline-block rounded-full bg-accent px-1.5 text-on-accent" : "text-tertiary"
        }`}
      >
        {day.getDate()}
      </span>
      {items.length > 0 && (
        <span data-testid="calendar-dots" aria-hidden="true" className="flex flex-wrap items-center gap-1">
          {items.slice(0, MAX_DOTS).map((item) => (
            <span
              key={item.key}
              data-testid="calendar-dot"
              data-state={item.state}
              className={`size-2 rounded-full ${DOT[item.state]}`}
            />
          ))}
          {extra > 0 && (
            <span data-testid="calendar-more" className="text-2xs leading-none text-tertiary tabular-nums">
              +{extra}
            </span>
          )}
        </span>
      )}
      {anchor && <DayPeek id={peekId} anchor={anchor} items={items} />}
    </button>
  );
}

/**
 * The month view: a whole month at a glance, a dot for everything on each day.
 *
 * Titled chips used to fill each cell: three truncated recordings, three matched events,
 * and a "+N" that counted only the recordings. At a month's cell width a title is ten
 * characters, which is not enough to recognise a meeting by and is enough to crowd out
 * everything else. Dots say how busy a day was and what state it is in; hovering or
 * focusing a day lists it, and pressing it opens that day.
 */
export default function MonthGrid({
  days,
  anchor,
  meetings,
  events = [],
  onDay,
}: {
  days: Date[];
  anchor: Date;
  meetings: Meeting[];
  /** Calendar events: a grey dot each, or the recording's colour once one is matched. */
  events?: CalendarEvent[];
  /** Opens a day in the day view. */
  onDay?: (day: Date) => void;
}) {
  const { locale } = useI18n();

  return (
    <div data-testid="calendar-monthgrid">
      <div className="grid grid-cols-7 gap-1.5 pb-1.5">
        {weekdayLabels(locale).map((label) => (
          <div key={label} className="text-center text-sm text-tertiary">
            {label}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {days.map((day) => (
          <DayCell
            key={dayKey(day)}
            day={day}
            items={dayItems(day, meetings, events)}
            outside={!isSameMonth(day, anchor)}
            onDay={onDay}
          />
        ))}
      </div>
    </div>
  );
}
