import { useRef, useState } from "react";
import { Outlet, useMatch, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { groupByDay } from "../lib/timeline";
import { formatDayLabel } from "../lib/format";
import { daysFor, periodLabel, rangeFor, shift, startOfDay, type CalendarSpan } from "../lib/calendar";
import MeetingCard from "../components/MeetingCard";
import MonthGrid from "../components/MonthGrid";
import TimeGrid from "../components/TimeGrid";
import EventDetails from "../components/EventDetails";
import type { CalendarEvent } from "../api";

type View = "list" | "calendar";

/** Explicit, so the message keys stay type-checked rather than cast away. */
const SPAN_LABEL = {
  day: "timeline.spanDay",
  week: "timeline.spanWeek",
  month: "timeline.spanMonth",
} as const;

interface UiConfig {
  ui?: { view?: View; calendar_span?: CalendarSpan };
}

/**
 * The library: the meeting list on the left, whatever you opened on the right.
 *
 * The list used to *be* the screen, and opening a meeting replaced it — so moving
 * between two meetings meant going back, finding your place, and going forward
 * again. Keeping it means the list is context rather than a destination, which is
 * how every application built around a collection of things works.
 *
 * The calendar takes over the detail side rather than the list, because it is a
 * different view of the same collection, not a different thing to look at.
 */
export default function Library() {
  const { t, locale } = useI18n();
  /*
   * On a narrow window the list yields to what you opened.
   *
   * The rail and the list cost 328px permanently, which is fine in a wide window
   * and most of a small one. Granola solves the same problem by closing its
   * sidebar the moment you open a note, and describes that one responsive rule as
   * what makes the app feel like an app rather than a page. The rail stays, so
   * getting back to the list is always one click.
   */
  // From the match rather than useParams: this is the layout route, and the id
  // belongs to the child, so useParams here is empty.
  const open = useMatch("/m/:id");
  const openId = open?.params.id;
  const reading = open !== null;
  const navigate = useNavigate();
  const listRef = useRef<HTMLDivElement | null>(null);
  const queryClient = useQueryClient();
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [override, setOverride] = useState<{ view?: View; span?: CalendarSpan }>({});

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const stored = ((settings.data?.config ?? {}) as UiConfig).ui ?? {};
  const view: View = override.view ?? stored.view ?? "list";
  const span: CalendarSpan = override.span ?? stored.calendar_span ?? "week";

  // Remembered the way the language setting is, so the screen opens as it was left.
  // Applied locally first: waiting for a round trip to redraw makes a toggle feel broken.
  const remember = useMutation({ mutationFn: (v: Record<string, unknown>) => api.putSettings(v) });
  const pickView = (next: View) => {
    setOverride((current) => ({ ...current, view: next }));
    remember.mutate({ "ui.view": next });
  };
  const pickSpan = (next: CalendarSpan) => {
    setOverride((current) => ({ ...current, span: next }));
    remember.mutate({ "ui.calendar_span": next });
  };

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
  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings() });
  const inRange = useQuery({
    queryKey: ["meetings", range.from, range.to],
    queryFn: () => api.meetings({ from: range.from, to: range.to, limit: "500" }),
    enabled: view === "calendar",
  });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  // Google Calendar events, from the local cache. Empty until an account is connected.
  const calendarEvents = useQuery({
    queryKey: ["calendar-events", range.from, range.to],
    queryFn: () => api.calendarEvents(range.from, range.to),
    enabled: view === "calendar",
  });
  const [openEvent, setOpenEvent] = useState<CalendarEvent | null>(null);
  const events = calendarEvents.data?.events ?? [];

  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteMeeting(id),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const items = meetings.data?.meetings ?? [];
  const windowed = inRange.data?.meetings ?? [];
  const byDay = groupByDay(items);
  const queued = status.data?.queue_depth ?? 0;

  /*
   * Keyboard movement through the list.
   *
   * Both j/k and the arrows, unmodified: cmdk ships vim bindings on by default,
   * Linear documents "arrow/J-K", Gmail is j and k. They are bare here rather than
   * Ctrl-prefixed because in a two-pane app the list owns focus, not a text field.
   *
   * Moving the selection swaps what the detail shows — it does not push a second
   * history entry per keystroke, or holding j would bury the back button under
   * fifty of them. Enter and `o` hand focus to the detail instead of navigating
   * again, because the thing is already on screen; the only thing left to do with
   * it is read it.
   */
  const move = (by: number) => {
    if (items.length === 0) return;
    const at = items.findIndex((meeting) => meeting.id === openId);
    const next = at === -1 ? 0 : Math.min(items.length - 1, Math.max(0, at + by));
    navigate(`/m/${items[next].id}`, { replace: at !== -1 });
  };

  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (event.target instanceof HTMLInputElement) return;
    const key = event.key;
    if (key === "j" || key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (key === "k" || key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (key === "Enter" || key === "o") {
      event.preventDefault();
      // Focus, not navigation: the detail is already showing.
      document.querySelector<HTMLElement>("[data-detail-pane]")?.focus();
    }
  };

  const toggle = (active: boolean) =>
    `rounded-sm px-2 py-0.5 text-2xs font-medium transition-colors ${
      active ? "bg-raised text-primary shadow-sm" : "text-secondary hover:text-primary"
    }`;

  return (
    <div className="flex min-w-0 flex-1" data-testid="library">
      <section
        data-testid="timeline"
        data-collapsed={reading ? "narrow" : undefined}
        className={`w-[280px] shrink-0 flex-col border-e border-line-subtle bg-surface-1 ${
          reading ? "hidden lg:flex" : "flex"
        }`}
      >
        <header className="flex items-center gap-2 px-3.5 pt-3.5 pb-2">
          <h1 className="text-md font-semibold tracking-tight">{t("nav.timeline")}</h1>
          <span className="text-2xs text-tertiary">{items.length}</span>

          {queued > 0 ? (
            <span
              data-testid="queue-depth"
              title={t("timeline.queued")}
              className="shrink-0 whitespace-nowrap rounded-full bg-surface-3 px-1.5 text-2xs text-secondary tabular-nums"
            >
              {t("timeline.queued")}: {queued}
            </span>
          ) : (
            <span className="sr-only" data-testid="queue-depth">
              {t("timeline.queued")}: 0
            </span>
          )}

          <div className="ms-auto flex gap-0.5 rounded-md bg-surface-3 p-0.5" data-testid="view-toggle">
            <button
              type="button"
              data-testid="view-list"
              aria-pressed={view === "list"}
              onClick={() => pickView("list")}
              className={toggle(view === "list")}
            >
              {t("timeline.viewList")}
            </button>
            <button
              type="button"
              data-testid="view-calendar"
              aria-pressed={view === "calendar"}
              onClick={() => pickView("calendar")}
              className={toggle(view === "calendar")}
            >
              {t("timeline.viewCalendar")}
            </button>
          </div>
        </header>

        <div
          ref={listRef}
          data-testid="meeting-list"
          role="listbox"
          aria-label={t("nav.timeline")}
          tabIndex={0}
          onKeyDown={onListKeyDown}
          className="ma-list min-h-0 flex-1 overflow-y-auto px-2 pb-3 outline-none"
        >
          {meetings.isLoading && (
            <p data-testid="loading" className="px-2 py-3 text-sm text-tertiary">
              {t("common.loading")}
            </p>
          )}
          {meetings.isError && (
            <p data-testid="error" className="px-2 py-3 text-sm text-danger">
              {t("common.error")}
            </p>
          )}
          {!meetings.isLoading && !meetings.isError && byDay.length === 0 && (
            <p data-testid="timeline-empty" className="px-2 py-10 text-center text-sm text-tertiary">
              {t("timeline.empty")}
            </p>
          )}

          {byDay.map(([day, dayItems]) => (
            <div key={day} data-testid="timeline-day" data-day={day} className="mb-1">
              <h2
                title={day}
                className="px-2 pt-3 pb-1 text-xs font-medium text-tertiary"
              >
                {formatDayLabel(day, locale, t)}
              </h2>
              {dayItems.map((meeting) => (
                <MeetingCard
                  key={meeting.id}
                  meeting={meeting}
                  selected={meeting.id === openId}
                  onStop={() => stop.mutate()}
                  stopping={stop.isPending}
                  onDelete={() => remove.mutate(meeting.id)}
                  deleting={remove.isPending && remove.variables === meeting.id}
                />
              ))}
            </div>
          ))}
        </div>
      </section>

      {/*
       * One tab stop, so the ring goes rail -> list -> detail -> transport, and a
       * real focus target so the detail scrolls with the keys it should:
       * forwarding PageDown and Home from the list would mean reimplementing them,
       * and would break text selection and scrollIntoView along the way.
       */}
      <section
        data-detail-pane
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !reading) return;
          if (event.key === "Escape") {
            event.preventDefault();
            listRef.current?.focus();
          }
        }}
        className="min-w-0 flex-1 overflow-y-auto outline-none"
      >
        {/*
         * The calendar is a different view of the list, so it lives on the detail
         * side — but only while nothing is open. It used to hold that side
         * unconditionally, which meant clicking a meeting in calendar view
         * navigated correctly and then rendered the calendar anyway: the URL
         * changed, the page did not, and the meeting appeared not to open at all.
         * Whatever you opened wins the pane.
         */}
        {view === "calendar" && !reading ? (
          <div className="p-6">
            <div className="mb-3 flex flex-wrap items-center gap-2" data-testid="calendar-controls">
              <button
                type="button"
                data-testid="calendar-prev"
                aria-label={t("timeline.previous")}
                onClick={() => setAnchor(shift(span, anchor, -1))}
                className="rounded-sm border border-line px-2 py-1 text-sm"
              >
                ‹
              </button>
              <button
                type="button"
                data-testid="calendar-today"
                onClick={() => setAnchor(startOfDay(new Date()))}
                className="rounded-sm border border-line px-2 py-1 text-sm"
              >
                {t("timeline.today")}
              </button>
              <button
                type="button"
                data-testid="calendar-next"
                aria-label={t("timeline.next")}
                onClick={() => setAnchor(shift(span, anchor, 1))}
                className="rounded-sm border border-line px-2 py-1 text-sm"
              >
                ›
              </button>
              <span data-testid="calendar-period" className="text-sm font-medium">
                {periodLabel(span, anchor, locale)}
              </span>
              <div className="ms-auto flex gap-0.5 rounded-md bg-surface-3 p-0.5">
                {(["day", "week", "month"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    data-testid={`span-${option}`}
                    aria-pressed={span === option}
                    onClick={() => pickSpan(option)}
                    className={toggle(span === option)}
                  >
                    {t(SPAN_LABEL[option])}
                  </button>
                ))}
              </div>
            </div>
            {span === "month" ? (
              <MonthGrid days={days} anchor={anchor} meetings={windowed} events={events} />
            ) : (
              <TimeGrid days={days} meetings={windowed} events={events} onEvent={setOpenEvent} />
            )}
            {openEvent && <EventDetails event={openEvent} onClose={() => setOpenEvent(null)} />}
          </div>
        ) : (
          <Outlet />
        )}
      </section>
    </div>
  );
}
