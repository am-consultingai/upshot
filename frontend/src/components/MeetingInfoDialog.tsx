import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import TimeField from "./TimeField";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MeetingDetail } from "../api";
import { useI18n } from "../i18n";
import {
  DEFAULT_FORMATS,
  DURATIONS,
  QUARTER_MS,
  durationLabel,
  floorToQuarter,
  formatTime,
  parseTime,
  quartersOfDay,
} from "../lib/timeFormat";
import Button from "./Button";

/**
 * The meeting's details: its title, a description, and when it starts and ends.
 *
 * Opened when a recording starts (the recording is already running; nothing here holds it
 * up), and from the meeting's menu later. Nothing is required: Done saves whatever was
 * written, closing without Done keeps nothing. The start is offered in quarter hours,
 * floored from the recording's start; the end as a duration, with the time it ends in
 * brackets. Times are written the way Windows shows them (GET /api/locale).
 */
let pending: string | null = null;
const listeners = new Set<() => void>();

function set(next: string | null) {
  pending = next;
  for (const listener of listeners) listener();
}

/** Show the details of this meeting. */
export function openMeetingInfo(meetingId: string): void {
  set(meetingId);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const DEFAULT_DURATION = 30;
const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_OVERNIGHT_MS = 8 * 60 * 60 * 1000;

export default function MeetingInfoHost() {
  const meetingId = useSyncExternalStore(subscribe, () => pending);
  if (!meetingId) return null;
  return <MeetingInfoDialog key={meetingId} meetingId={meetingId} onClose={() => set(null)} />;
}

function MeetingInfoDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const meeting = useQuery({
    queryKey: ["meeting", meetingId],
    queryFn: () => api.meeting(meetingId),
  });
  const formats = useQuery({
    queryKey: ["locale"],
    queryFn: api.locale,
    staleTime: Infinity,
  });
  const fmt = formats.data ?? DEFAULT_FORMATS;
  const data = meeting.data;
  if (!data) return null;
  return (
    <Form
      meeting={data}
      time={(when) => formatTime(when, fmt)}
      onClose={onClose}
      onSaved={() => {
        void queryClient.invalidateQueries();
        onClose();
      }}
      t={t}
    />
  );
}

function Form({
  meeting,
  time,
  onClose,
  onSaved,
  t,
}: {
  meeting: MeetingDetail;
  time: (when: Date) => string;
  onClose: () => void;
  onSaved: () => void;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const day = useMemo(
    () => new Date(meeting.planned_start ?? meeting.started_at),
    [meeting.planned_start, meeting.started_at],
  );
  // A start already set stays as typed; a fresh one is the quarter hour the recording began in.
  const initialStart = useMemo(() => (meeting.planned_start ? day : floorToQuarter(day)), [meeting.planned_start, day]);
  const initialEnd = useMemo(
    () =>
      meeting.planned_end ? new Date(meeting.planned_end) : new Date(initialStart.getTime() + DEFAULT_DURATION * 60000),
    [meeting.planned_end, initialStart],
  );

  const [title, setTitle] = useState(meeting.title ?? "");
  const [description, setDescription] = useState(meeting.description ?? "");
  // Text the user can type anything into; the lists under them are only suggestions.
  const [startText, setStartText] = useState(time(initialStart));
  const [endText, setEndText] = useState(time(initialEnd));
  const [endTouched, setEndTouched] = useState(!!meeting.planned_end);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // Once, when the dialog opens. Re-running this on every render (the page refetches in
  // the background) pulled the focus back to the title while the user typed elsewhere.
  useEffect(() => {
    titleRef.current?.focus();
    titleRef.current?.select();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const start = parseTime(startText, day);
  const end = parseTime(endText, day);
  const slots = quartersOfDay(day);
  const units = { h: t("meetingInfo.hours"), min: t("meetingInfo.minutes") };
  const endSuggestions = start
    ? DURATIONS.map((minutes) => ({
        at: new Date(start.getTime() + minutes * 60000),
        minutes,
      }))
    : [];

  const changeStart = (text: string) => {
    setStartText(text);
    // Until the end has been set by hand, it keeps its distance from the start.
    const next = parseTime(text, day);
    if (next && !endTouched) setEndText(time(new Date(next.getTime() + DEFAULT_DURATION * 60000)));
  };

  const save = async () => {
    setError(null);
    const body: Record<string, unknown> = { description };
    const startAt = startText.trim() ? parseTime(startText, day) : null;
    let endAt = endText.trim() ? parseTime(endText, day) : null;
    // 23:30 to 00:30 ends the next day; an end hours before the start is a mistake.
    if (startAt && endAt && endAt <= startAt && endAt.getTime() + DAY_MS - startAt.getTime() <= MAX_OVERNIGHT_MS) {
      endAt = new Date(endAt.getTime() + DAY_MS);
    }
    if (startText.trim() && !startAt) return setError(t("meetingInfo.badStart"));
    if (endText.trim() && !endAt) return setError(t("meetingInfo.badEnd"));
    if (startAt && endAt && endAt <= startAt) return setError(t("meetingInfo.endBeforeStart"));
    body.planned_start = startAt ? isoWithOffset(startAt) : "";
    body.planned_end = endAt ? isoWithOffset(endAt) : "";
    const typed = title.trim();
    if (typed && typed !== (meeting.title ?? "")) body.title = typed;
    setSaving(true);
    try {
      await api.patchMeeting(meeting.id, body);
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setSaving(false);
    }
  };

  const field =
    "w-full rounded-md bg-transparent px-2 py-1.5 text-sm text-primary shadow-[var(--shadow-ring)] outline-none";
  return createPortal(
    <div className="fixed inset-0 z-[80] grid place-items-center bg-scrim-soft px-4 backdrop-blur-xs">
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="meeting-info-title"
        data-testid="meeting-info"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        className="ma-dialog w-full max-w-110 space-y-3 rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <h2 id="meeting-info-title" className="text-md font-semibold tracking-snug">
          {t("meetingInfo.heading")}
        </h2>
        <label className="block space-y-1 text-xs text-secondary">
          <span>{t("meetingInfo.title")}</span>
          <input
            ref={titleRef}
            data-testid="meeting-info-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className={field}
          />
        </label>
        <label className="block space-y-1 text-xs text-secondary">
          <span>{t("meetingInfo.description")}</span>
          <textarea
            data-testid="meeting-info-description"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className={field}
          />
        </label>
        <div className="flex gap-3">
          {/* Not a <label>: a click on a suggestion would be passed on to the box. */}
          <div className="flex-1 space-y-1 text-xs text-secondary">
            <span>{t("meetingInfo.start")}</span>
            <TimeField
              label={t("meetingInfo.start")}
              testId="meeting-info-start"
              value={startText}
              onChange={changeStart}
              current={start}
              options={slots.map((slot) => ({ value: time(slot), at: slot }))}
              className={field}
            />
          </div>
          <div className="flex-1 space-y-1 text-xs text-secondary">
            <span>{t("meetingInfo.end")}</span>
            <TimeField
              label={t("meetingInfo.end")}
              testId="meeting-info-end"
              value={endText}
              onChange={(text) => {
                setEndTouched(true);
                setEndText(text);
              }}
              current={end}
              options={endSuggestions.map(({ at, minutes }) => ({
                value: time(at),
                at,
                label: durationLabel(minutes, units),
              }))}
              className={field}
            />
          </div>
        </div>
        <p className="text-xs text-tertiary">{t("meetingInfo.optional")}</p>
        {error && (
          <p data-testid="meeting-info-error" className="text-xs text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            data-testid="meeting-info-cancel"
            onClick={onClose}
            variant="ghost"
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="submit"
            data-testid="meeting-info-done"
            disabled={saving}
            variant="primary"
          >
            {t("meetingInfo.done")}
          </Button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/** "2026-09-30T14:00:00+03:00": local time with its offset, as the API wants. */
export function isoWithOffset(when: Date): string {
  const offset = -when.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}` +
    `T${pad(when.getHours())}:${pad(when.getMinutes())}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

export { QUARTER_MS };
