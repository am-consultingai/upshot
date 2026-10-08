/**
 * What the one worker is doing and what waits for it (`/api/jobs/active`): meetings and
 * files together, in the order they will run, with percent done and time left where
 * the server can say. The meeting card, the meeting page and Settings all read this one
 * list, so they never disagree about how far a transcription has got.
 *
 * Live through two events: `job` with `action: "progress"` (a meeting's transcribe
 * stage, at most once a second) and `transcription` (a file). Both patch this cache in
 * place; anything else — a job starting, ending, appearing — refetches it.
 */
import { useQuery, type QueryClient } from "@tanstack/react-query";
import { api, type ActiveJob } from "../api";
import { formatDuration } from "./format";
import type { MessageKey } from "../locales/en";

type Translate = (key: MessageKey) => string;

export const ACTIVE_KEY = ["jobs", "active"] as const;

/** The list, polled only while something is in it: an empty queue is woken by events. */
export function useActiveJobs() {
  return useQuery({
    queryKey: ACTIVE_KEY,
    queryFn: api.activeJobs,
    refetchInterval: (query) => ((query.state.data?.jobs.length ?? 0) > 0 ? 5000 : false),
  });
}

/** This meeting's job in the queue, running or waiting; undefined when it has none. */
export function useMeetingJob(meetingId: string): ActiveJob | undefined {
  const active = useActiveJobs();
  return active.data?.jobs.find((job) => job.kind === "meeting" && job.id === meetingId);
}

export interface JobProgressEvent {
  action: "progress";
  meeting_id: string;
  stage: string;
  phase: string | null;
  progress: number;
  eta_s: number | null;
}

export interface FileProgressEvent {
  id: string;
  state: string;
  phase: string | null;
  progress: number;
  eta_s?: number | null;
}

function patch(
  client: QueryClient,
  match: (job: ActiveJob) => boolean,
  change: Partial<ActiveJob>,
  state?: string,
): void {
  const list = client.getQueryData<{ jobs: ActiveJob[] }>(ACTIVE_KEY);
  const known = list?.jobs.find(match);
  if (!list || !known || (state !== undefined && known.state !== state)) {
    // New to the list, or a new state: order and positions change with it.
    void client.invalidateQueries({ queryKey: ACTIVE_KEY, exact: true });
    return;
  }
  client.setQueryData(ACTIVE_KEY, { jobs: list.jobs.map((job) => (match(job) ? { ...job, ...change } : job)) });
}

/** A meeting's progress event, applied to the list and nothing else. */
export function applyJobProgress(client: QueryClient, event: JobProgressEvent): void {
  patch(
    client,
    (job) => job.kind === "meeting" && job.id === event.meeting_id && job.stage === event.stage,
    { state: "running", phase: event.phase, progress: event.progress, eta_s: event.eta_s },
    "running",
  );
}

/** A file's `transcription` event, as the Transcriptions page applies it to its own list. */
export function applyFileEvent(client: QueryClient, event: FileProgressEvent): void {
  if (event.state !== "running") {
    // Started waiting, finished, cancelled or deleted: the list is a different list now.
    void client.invalidateQueries({ queryKey: ACTIVE_KEY, exact: true });
    return;
  }
  patch(
    client,
    (job) => job.kind === "file" && job.id === event.id,
    { phase: event.phase, progress: event.progress, eta_s: event.eta_s ?? null },
    "running",
  );
}

/** What each meeting stage is doing, in words. */
export const STAGE_LABEL: Record<string, MessageKey> = {
  transcribe: "meeting.stageTranscribe",
  assemble: "meeting.stageAssemble",
  summarize: "meeting.stageSummarize",
  render: "meeting.stageRender",
  deliver: "meeting.stageDeliver",
};

const PHASE_LABEL: Record<string, MessageKey> = {
  prepare: "progress.phase.prepare",
  decode: "transcriptions.phase.decode",
  check: "transcriptions.phase.check",
  language: "transcriptions.phase.language",
  transcribe: "transcriptions.phase.transcribe",
  diarize: "transcriptions.phase.diarize",
};

const REASONS: Record<string, MessageKey> = {
  recording: "transcriptions.waitingRecording",
  "policy:when_idle": "transcriptions.waitingIdle",
  "policy:scheduled": "transcriptions.waitingScheduled",
  retry: "transcriptions.waitingRetry",
};

const CLIENTS: Record<ActiveJob["client"], MessageKey> = {
  meeting: "activeJobs.client.meeting",
  ui: "transcriptions.client.ui",
  api: "transcriptions.client.api",
  mcp: "transcriptions.client.mcp",
};

export function phaseLabel(phase: string | null | undefined, t: Translate): string | null {
  const key = phase ? PHASE_LABEL[phase] : undefined;
  return key ? t(key) : null;
}

/** "42%", as the locale writes a percentage. */
export function formatPercent(progress: number, locale: string): string {
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(
    Math.max(0, Math.min(1, progress)),
  );
}

/** "~6 min left"; nothing until the server has measured enough of the run to say. */
export function timeLeft(etaS: number | null | undefined, t: Translate): string | null {
  if (etaS === null || etaS === undefined) return null;
  if (etaS < 60) return t("progress.leftUnderMinute");
  return t("progress.left").replace("{time}", formatDuration(etaS, t));
}

export function clientName(job: ActiveJob, t: Translate): string {
  return t(CLIENTS[job.client] ?? "transcriptions.client.api");
}

/**
 * Where a job is, in one line: "Transcribing · separating speakers · 42%", or why it
 * waits. A stage with no percentage (a summary) is named and left at that.
 */
export function jobStatus(job: ActiveJob, t: Translate, locale: string): string {
  if (job.state === "pending") {
    const reason = job.waiting_reason ? REASONS[job.waiting_reason] : undefined;
    if (reason) return t(reason);
    return t("transcriptions.waiting").replace("{n}", String(job.position ?? 1));
  }
  const parts = [t(job.kind === "file" ? "meeting.stageTranscribe" : (STAGE_LABEL[job.stage ?? ""] ?? "meeting.stageWorking"))];
  const phase = phaseLabel(job.phase, t);
  if (phase) parts.push(phase);
  if (job.progress !== null) parts.push(formatPercent(job.progress, locale));
  return parts.join(" · ");
}
