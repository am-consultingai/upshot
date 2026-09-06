import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { groupByDay } from "../lib/timeline";
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

export default function Timeline() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [override, setOverride] = useState<{ view?: View; span?: CalendarSpan }>({});

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const stored = ((settings.data?.config ?? {}) as UiConfig).ui ?? {};
  const view: View = override.view ?? stored.view ?? "list";
  const span: CalendarSpan = override.span ?? stored.calendar_span ?? "week";

  // Remember the choice the way the language setting is remembered, so the main screen
  // opens the way it was left. Applied locally first: waiting for a round trip to
  // redraw makes the toggle feel broken.
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
  // The list shows everything; the calendar fetches only the period on screen.
  const meetings = useQuery({
    queryKey: view === "calendar" ? ["meetings", range.from, range.to] : ["meetings"],
    queryFn: () =>
      view === "calendar"
        ? api.meetings({ from: range.from, to: range.to, limit: "500" })
        : api.meetings(),
  });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });

  const start = useMutation({
    mutationFn: api.startRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteMeeting(id),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const items = meetings.data?.meetings ?? [];
  const days_ = groupByDay(items);

  const toggle = (active: boolean) =>
    `rounded border px-2 py-1 text-sm ${
      active ? "border-neutral-800 bg-neutral-800 text-white" : "border-neutral-300"
    }`;

  return (
    <section data-testid="timeline">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="start-recording"
          disabled={status.data?.recorder.active}
          onClick={() => start.mutate()}
          className="rounded bg-neutral-900 px-3 py-1.5 text-white disabled:opacity-40"
        >
          {t("timeline.start")}
        </button>
        <span className="text-sm text-neutral-600" data-testid="queue-depth">
          {t("timeline.queued")}: {status.data?.queue_depth ?? 0}
        </span>

        <div className="ms-auto flex gap-2" data-testid="view-toggle">
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
      </div>

      {view === "calendar" && (
        <div className="mb-3 flex flex-wrap items-center gap-2" data-testid="calendar-controls">
          <button
            type="button"
            data-testid="calendar-prev"
            aria-label={t("timeline.previous")}
            onClick={() => setAnchor(shift(span, anchor, -1))}
            className="rounded border border-neutral-300 px-2 py-1 text-sm"
          >
            ‹
          </button>
          <button
            type="button"
            data-testid="calendar-today"
            onClick={() => setAnchor(startOfDay(new Date()))}
            className="rounded border border-neutral-300 px-2 py-1 text-sm"
          >
            {t("timeline.today")}
          </button>
          <button
            type="button"
            data-testid="calendar-next"
            aria-label={t("timeline.next")}
            onClick={() => setAnchor(shift(span, anchor, 1))}
            className="rounded border border-neutral-300 px-2 py-1 text-sm"
          >
            ›
          </button>
          <span data-testid="calendar-period" className="text-sm font-medium">
            {periodLabel(span, anchor, locale)}
          </span>
          <div className="ms-auto flex gap-2">
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
      )}

      {meetings.isLoading && <p data-testid="loading">{t("common.loading")}</p>}
      {meetings.isError && <p data-testid="error">{t("common.error")}</p>}

      {!meetings.isLoading && !meetings.isError && view === "calendar" && (
        span === "month" ? (
          <MonthGrid days={days} anchor={anchor} meetings={items} />
        ) : (
          <TimeGrid days={days} meetings={items} />
        )
      )}

      {!meetings.isLoading && !meetings.isError && view === "list" && (
        <>
          {days_.length === 0 && <p data-testid="timeline-empty">{t("timeline.empty")}</p>}
          {days_.map(([day, dayItems]) => (
            <div key={day} data-testid="timeline-day" data-day={day} className="mb-6">
              <h2 className="mb-2 text-sm font-semibold text-neutral-500">{day}</h2>
              <div className="grid gap-2">
                {dayItems.map((meeting) => (
                  <MeetingCard
                    key={meeting.id}
                    meeting={meeting}
                    onStop={() => stop.mutate()}
                    onDelete={() => remove.mutate(meeting.id)}
                  />
                ))}
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  );
}
