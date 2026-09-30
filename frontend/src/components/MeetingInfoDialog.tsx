import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
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
  quartersOfDay,
} from "../lib/timeFormat";

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

export default function MeetingInfoHost() {
  const meetingId = useSyncExternalStore(subscribe, () => pending);
  if (!meetingId) return null;
  return <MeetingInfoDialog key={meetingId} meetingId={meetingId} onClose={() => set(null)} />;
}

function MeetingInfoDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const meeting = useQuery({ queryKey: ["meeting", meetingId], queryFn: () => api.meeting(meetingId) });
  const formats = useQuery({ queryKey: ["locale"], queryFn: api.locale, staleTime: Infinity });
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
  const initialStart = useMemo(
    () => floorToQuarter(new Date(meeting.planned_start ?? meeting.started_at)),
    [meeting.planned_start, meeting.started_at],
  );
  const initialDuration = useMemo(() => {
    if (!meeting.planned_start || !meeting.planned_end) return DEFAULT_DURATION;
    const minutes = (new Date(meeting.planned_end).getTime() - new Date(meeting.planned_start).getTime()) / 60000;
    return Math.min(480, Math.max(15, Math.round(minutes / 15) * 15));
  }, [meeting.planned_start, meeting.planned_end]);

  const [title, setTitle] = useState(meeting.title ?? "");
  const [description, setDescription] = useState(meeting.description ?? "");
  const [start, setStart] = useState(initialStart.getTime());
  const [duration, setDuration] = useState(initialDuration);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    titleRef.current?.focus();
    titleRef.current?.select();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const slots = quartersOfDay(initialStart);
  const units = { h: t("meetingInfo.hours"), min: t("meetingInfo.minutes") };

  const save = async () => {
    setSaving(true);
    setError(null);
    const body: Record<string, unknown> = {
      description,
      planned_start: isoWithOffset(new Date(start)),
      planned_end: isoWithOffset(new Date(start + duration * 60000)),
    };
    const typed = title.trim();
    if (typed && typed !== (meeting.title ?? "")) body.title = typed;
    try {
      await api.patchMeeting(meeting.id, body);
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setSaving(false);
    }
  };

  const field = "w-full rounded-md bg-transparent px-2 py-1.5 text-sm text-primary shadow-[var(--shadow-ring)] outline-none";
  return createPortal(
    <div className="fixed inset-0 z-[80] grid place-items-center bg-scrim-soft px-4 backdrop-blur-[4px]">
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="meeting-info-title"
        data-testid="meeting-info"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        className="ma-dialog w-full max-w-[440px] space-y-3 rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <h2 id="meeting-info-title" className="text-md font-semibold tracking-snug">
          {t("meetingInfo.heading")}
        </h2>
        <label className="block space-y-1 text-xs text-secondary">
          <span>{t("meetingInfo.title")}</span>
          <input ref={titleRef} data-testid="meeting-info-title" value={title} onChange={(e) => setTitle(e.target.value)} className={field} />
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
          <label className="block flex-1 space-y-1 text-xs text-secondary">
            <span>{t("meetingInfo.start")}</span>
            <select data-testid="meeting-info-start" value={start} onChange={(e) => setStart(Number(e.target.value))} className={field}>
              {slots.map((slot) => (
                <option key={slot.getTime()} value={slot.getTime()}>
                  {time(slot)}
                </option>
              ))}
            </select>
          </label>
          <label className="block flex-1 space-y-1 text-xs text-secondary">
            <span>{t("meetingInfo.end")}</span>
            <select data-testid="meeting-info-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))} className={field}>
              {DURATIONS.map((minutes) => (
                <option key={minutes} value={minutes}>
                  {durationLabel(minutes, units)} ({time(new Date(start + minutes * 60000))})
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="text-xs text-tertiary">{t("meetingInfo.optional")}</p>
        {error && (
          <p data-testid="meeting-info-error" className="text-xs text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            data-testid="meeting-info-cancel"
            onClick={onClose}
            className="h-7 rounded-md px-2.5 text-sm font-medium text-primary hover:bg-a-200 active:bg-a-300"
          >
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            data-testid="meeting-info-done"
            disabled={saving}
            className="h-7 rounded-md bg-accent px-3 text-sm font-medium text-on-accent disabled:opacity-60"
          >
            {t("meetingInfo.done")}
          </button>
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
