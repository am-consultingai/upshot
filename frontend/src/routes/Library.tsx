import { useState } from "react";
import { Outlet } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { groupByDay } from "../lib/timeline";
import { formatDayLabel } from "../lib/format";
import { daysFor, periodLabel, rangeFor, shift, startOfDay, type CalendarSpan } from "../lib/calendar";
import MeetingCard from "../components/MeetingCard";
import MonthGrid from "../components/MonthGrid";
import TimeGrid from "../components/TimeGrid";

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
  const meetings = useQuery({
    queryKey: view === "calendar" ? ["meetings", range.from, range.to] : ["meetings"],
    queryFn: () =>
      view === "calendar"
        ? api.meetings({ from: range.from, to: range.to, limit: "500" })
        : api.meetings(),
  });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });

  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteMeeting(id),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const items = meetings.data?.meetings ?? [];
  const byDay = groupByDay(items);
  const queued = status.data?.queue_depth ?? 0;

  const toggle = (active: boolean) =>
    `rounded-sm px-2 py-0.5 text-2xs font-medium transition-colors ${
      active ? "bg-raised text-primary shadow-sm" : "text-secondary hover:text-primary"
    }`;

  return (
    <div className="flex min-w-0 flex-1" data-testid="library">
      <section
        data-testid="timeline"
        className="flex w-80 shrink-0 flex-col border-e border-line-subtle bg-surface-1"
      >
        <header className="flex items-center gap-2 px-3.5 pt-3.5 pb-2">
          <h1 className="text-md font-semibold tracking-tight">{t("nav.timeline")}</h1>
          <span className="text-2xs text-tertiary">{items.length}</span>

          {queued > 0 ? (
            <span
              data-testid="queue-depth"
              title={t("timeline.queued")}
              className="rounded-full bg-surface-3 px-1.5 text-2xs text-secondary tabular-nums"
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

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
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

      <section className="min-w-0 flex-1 overflow-y-auto">
        {view === "calendar" ? (
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
              <MonthGrid days={days} anchor={anchor} meetings={items} />
            ) : (
              <TimeGrid days={days} meetings={items} />
            )}
          </div>
        ) : (
          <Outlet />
        )}
      </section>
    </div>
  );
}
