import { useEffect, useState } from "react";
import { Outlet, useMatch } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import {
  daysFor,
  isoWeek,
  periodParts,
  rangeFor,
  shift,
  startOfDay,
  type CalendarSpan,
} from "../lib/calendar";
import MonthGrid from "../components/MonthGrid";
import TimeGrid from "../components/TimeGrid";
import AgendaList from "../components/AgendaList";
import LibraryRail from "../components/LibraryRail";
import EventDetails from "../components/EventDetails";
import Tooltip from "../components/Tooltip";
import type { CalendarEvent } from "../api";

/** Explicit, so the message keys stay type-checked rather than cast away. */
const SPAN_LABEL = {
  day: "timeline.spanDay",
  week: "timeline.spanWeek",
  month: "timeline.spanMonth",
  list: "timeline.spanList",
} as const;

/** One key per view, as Google Calendar and Amie bind them. */
const SPAN_KEY: Record<string, CalendarSpan> = { d: "day", w: "week", m: "month", l: "list" };

interface UiConfig {
  ui?: { calendar_span?: CalendarSpan };
}

/** Typing into a field is not a shortcut. */
function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

/**
 * The library: the meeting list on the left, whatever you opened on the right.
 *
 * The list used to *be* the screen, and opening a meeting replaced it — so moving
 * between two meetings meant going back, finding your place, and going forward
 * again. Keeping it means the list is context rather than a destination, which is
 * how every application built around a collection of things works.
 *
 * The detail side holds the calendar until something is opened on it, and the
 * calendar carries a rail of its own: what is recording, what is next, and what is
 * still owed. That width used to go to the grid, which on a 1440px window made seven
 * columns 160px wide to hold chips that are 90px of text — the rail is what the
 * calendar has to *say*, rather than more room for what it already shows.
 */
export default function Library() {
  const { t, locale } = useI18n();
  // From the match rather than useParams: this is the layout route, and the id
  // belongs to the child, so useParams here is empty.
  const open = useMatch("/m/:id");
  const reading = open !== null;
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [override, setOverride] = useState<{ span?: CalendarSpan }>({});

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const stored = ((settings.data?.config ?? {}) as UiConfig).ui ?? {};
  const span: CalendarSpan = override.span ?? stored.calendar_span ?? "week";

  // Remembered the way the language setting is, so the screen opens as it was left.
  // Applied locally first: waiting for a round trip to redraw makes a toggle feel broken.
  const remember = useMutation({ mutationFn: (v: Record<string, unknown>) => api.putSettings(v) });
  const pickSpan = (next: CalendarSpan) => {
    setOverride((current) => ({ ...current, span: next }));
    remember.mutate({ "ui.calendar_span": next });
  };

  /*
   * T goes to today, D/W/M/L pick the view, and the arrows walk the period.
   *
   * The button reads "T" rather than "Today" for exactly this reason: the label is
   * the shortcut, so pressing it teaches it. Only while the calendar is showing —
   * with a meeting open these letters belong to the page you are reading.
   */
  useEffect(() => {
    if (reading) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return;
      if (document.querySelector("[role=dialog],[role=alertdialog],[role=menu]")) return;
      const key = event.key.toLowerCase();
      if (key === "t") {
        event.preventDefault();
        setAnchor(startOfDay(new Date()));
      } else if (SPAN_KEY[key]) {
        event.preventDefault();
        pickSpan(SPAN_KEY[key]);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // pickSpan is recreated every render and only ever closes over setters.
  });

  const days = daysFor(span, anchor);
  const range = rangeFor(span, anchor);
  /*
   * The list is always the whole library; the calendar asks for its own window.
   *
   * These used to be one query, so opening the calendar silently range-filtered the
   * list beside it: the header went from "Timeline 7" to "Timeline 4" with nothing
   * to explain it, which reads as meetings having been deleted. The list is the
   * thing you navigate with and it should not change because a different pane did.
   */
  const inRange = useQuery({
    queryKey: ["meetings", range.from, range.to],
    queryFn: () => api.meetings({ from: range.from, to: range.to, limit: "500" }),
    enabled: !reading,
  });
  // Google Calendar events, from the local cache. Empty until an account is connected.
  const calendarEvents = useQuery({
    queryKey: ["calendar-events", range.from, range.to],
    queryFn: () => api.calendarEvents(range.from, range.to),
    enabled: !reading,
  });
  const [openEvent, setOpenEvent] = useState<CalendarEvent | null>(null);
  const events = calendarEvents.data?.events ?? [];
  const windowed = inRange.data?.meetings ?? [];
  const period = periodParts(span, anchor, locale);

  return (
    <div className="flex min-w-0 flex-1" data-testid="library">
      <section
        data-detail-pane
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !reading) return;
          if (event.key === "Escape") {
            event.preventDefault();
            document.querySelector<HTMLElement>("[data-testid=meeting-list]")?.focus();
          }
        }}
        className={`flex min-w-0 flex-1 flex-col outline-none ${reading ? "overflow-y-auto" : "overflow-hidden"}`}
      >
        {/*
         * The calendar is a different view of the list, so it lives on the detail
         * side — but only while nothing is open. Whatever you opened wins the pane.
         */}
        {!reading ? (
          <>
            <div
              data-testid="calendar-controls"
              className="flex h-13 flex-none items-center gap-2 border-b border-line-subtle px-4"
            >
              {/* Month in weight, year in grey: the part that changes as you page. */}
              <span data-testid="calendar-period" className="text-md tracking-snug">
                <b className="font-semibold">{period.main}</b>{" "}
                <span className="text-tertiary">{period.year}</span>
              </span>
              <div className="flex gap-px">
                <button
                  type="button"
                  data-testid="calendar-prev"
                  aria-label={t("timeline.previous")}
                  title={t("timeline.previous")}
                  onClick={() => setAnchor(shift(span, anchor, -1))}
                  className="grid size-6.5 place-items-center rounded-sm text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300"
                >
                  <svg viewBox="0 0 16 16" className="size-[15px] fill-none stroke-current stroke-[1.5] rtl:-scale-x-100">
                    <path d="M10 3.5 5.5 8l4.5 4.5" />
                  </svg>
                </button>
                <button
                  type="button"
                  data-testid="calendar-next"
                  aria-label={t("timeline.next")}
                  title={t("timeline.next")}
                  onClick={() => setAnchor(shift(span, anchor, 1))}
                  className="grid size-6.5 place-items-center rounded-sm text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300"
                >
                  <svg viewBox="0 0 16 16" className="size-[15px] fill-none stroke-current stroke-[1.5] rtl:-scale-x-100">
                    <path d="m6 3.5 4.5 4.5L6 12.5" />
                  </svg>
                </button>
              </div>
              <Tooltip label={t("timeline.jumpToday")} keys="T">
                <button
                  type="button"
                  data-testid="calendar-today"
                  aria-label={t("timeline.jumpToday")}
                  onClick={() => setAnchor(startOfDay(new Date()))}
                  className="h-6.5 rounded-sm px-2.25 font-mono text-xs text-secondary shadow-[var(--shadow-ring)] hover:bg-a-200 hover:text-primary active:bg-a-300"
                >
                  T
                </button>
              </Tooltip>
              {span !== "month" && span !== "list" && (
                <Tooltip label={`W${isoWeek(anchor)}`} hint={t("help.weekNumber")}>
                <span
                  data-testid="calendar-week"
                  className="rounded-full px-1.5 py-0.5 font-mono text-2xs text-tertiary shadow-[var(--shadow-ring-subtle)]"
                >
                  W{isoWeek(anchor)}
                </span>
                </Tooltip>
              )}
              <div className="ms-auto flex gap-0.5 rounded-md bg-surface-3 p-0.5" role="group">
                {(["day", "week", "month", "list"] as const).map((option) => (
                  <Tooltip
                    key={option}
                    label={t(SPAN_LABEL[option])}
                    keys={option[0].toUpperCase()}
                    hint={option === "list" ? t("help.spanList") : undefined}
                  >
                  <button
                    type="button"
                    data-testid={`span-${option}`}
                    aria-pressed={span === option}
                    onClick={() => pickSpan(option)}
                    className={`h-6 rounded-xs px-2.5 text-xs ${
                      span === option
                        ? "bg-raised text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]"
                        : "text-secondary hover:bg-a-200 hover:text-primary active:bg-a-300"
                    }`}
                  >
                    {t(SPAN_LABEL[option])}
                  </button>
                  </Tooltip>
                ))}
              </div>
            </div>
            <div className="flex min-h-0 flex-1">
              <div className="min-w-0 flex-1 overflow-hidden">
                {span === "month" ? (
                  <div className="h-full overflow-y-auto p-4">
                    <MonthGrid days={days} anchor={anchor} meetings={windowed} events={events} />
                  </div>
                ) : span === "list" ? (
                  <AgendaList days={days} meetings={windowed} events={events} onEvent={setOpenEvent} />
                ) : (
                  <TimeGrid days={days} meetings={windowed} events={events} onEvent={setOpenEvent} />
                )}
              </div>
              <LibraryRail />
            </div>
            {openEvent && <EventDetails event={openEvent} onClose={() => setOpenEvent(null)} />}
          </>
        ) : (
          <Outlet />
        )}
      </section>
    </div>
  );
}
