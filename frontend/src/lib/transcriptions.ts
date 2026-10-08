/**
 * File transcription on the page (D86): uploads with progress, what each state says,
 * and how live events reach the list without refetching the rest of the app.
 */
import type { QueryClient } from "@tanstack/react-query";
import { csrfToken, type Transcription } from "../api";
import type { MessageKey } from "../locales/en";

type Translate = (key: MessageKey) => string;

/** Download formats, in the order the menu offers them. */
export const DOWNLOAD_FORMATS = ["txt", "srt", "vtt", "json", "md"] as const;
export type DownloadFormat = (typeof DOWNLOAD_FORMATS)[number];

export const LIST_KEY = ["transcriptions"] as const;

export interface UploadOptions {
  language: string;
  diarize: boolean;
  prompt: string;
}

export const DEFAULT_OPTIONS: UploadOptions = { language: "auto", diarize: true, prompt: "" };

const OPTIONS_KEY = "upshot.transcription.options";

/** What was chosen last time, in this browser: a per-browser preference, not a setting. */
export function loadOptions(storage: Pick<Storage, "getItem"> | null = safeStorage()): UploadOptions {
  try {
    const raw = storage?.getItem(OPTIONS_KEY);
    if (!raw) return { ...DEFAULT_OPTIONS };
    const saved = JSON.parse(raw) as Partial<UploadOptions>;
    return {
      language: typeof saved.language === "string" ? saved.language : DEFAULT_OPTIONS.language,
      diarize: typeof saved.diarize === "boolean" ? saved.diarize : DEFAULT_OPTIONS.diarize,
      prompt: typeof saved.prompt === "string" ? saved.prompt : DEFAULT_OPTIONS.prompt,
    };
  } catch {
    return { ...DEFAULT_OPTIONS };
  }
}

export function saveOptions(options: UploadOptions, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(OPTIONS_KEY, JSON.stringify(options));
  } catch {
    // A private window or a full quota: the choice is only forgotten.
  }
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** The multipart body the API takes: the file, then the options as plain fields. */
export function uploadBody(file: Blob, name: string, options: UploadOptions): FormData {
  const body = new FormData();
  body.append("language", options.language);
  body.append("diarize", options.diarize ? "true" : "false");
  if (options.prompt.trim()) body.append("prompt", options.prompt.trim());
  body.append("file", file, name);
  return body;
}

export interface Upload {
  done: Promise<Transcription>;
  abort: () => void;
}

/**
 * One file, with upload progress. XHR rather than fetch: fetch still reports nothing
 * while a request body goes up, and a 2 GB video needs a progress bar.
 */
export function upload(
  file: File,
  options: UploadOptions,
  onProgress: (fraction: number) => void,
  makeRequest: () => XMLHttpRequest = () => new XMLHttpRequest(),
): Upload {
  const xhr = makeRequest();
  const done = new Promise<Transcription>((resolve, reject) => {
    xhr.open("POST", "/api/v1/transcriptions");
    xhr.setRequestHeader("X-CSRF-Token", csrfToken());
    xhr.setRequestHeader("X-Upshot-Client", "ui");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Transcription);
        return;
      }
      reject(new Error(detailOf(xhr.responseText) ?? `${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("network"));
    xhr.onabort = () => reject(new Error("aborted"));
    xhr.send(uploadBody(file, file.name, options));
  });
  return { done, abort: () => xhr.abort() };
}

function detailOf(text: string): string | null {
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : null;
  } catch {
    return text || null;
  }
}

const PHASES: Record<NonNullable<Transcription["phase"]>, MessageKey> = {
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

/** What a row says about where its job is. */
export function stateLabel(job: Transcription, t: Translate): string {
  switch (job.state) {
    case "pending": {
      const reason = job.waiting_reason ? REASONS[job.waiting_reason] : undefined;
      if (reason) return t(reason);
      return t("transcriptions.waiting").replace("{n}", String(job.position ?? 1));
    }
    case "running":
      return t("transcriptions.running")
        .replace("{phase}", t(PHASES[job.phase ?? "decode"]))
        .replace("{n}", String(Math.round(job.progress * 100)));
    case "done":
      return t("transcriptions.done");
    case "failed":
      return t("transcriptions.failed");
    case "cancelled":
      return t("transcriptions.cancelled");
  }
}

const CLIENTS: Record<Transcription["client"], MessageKey> = {
  ui: "transcriptions.client.ui",
  api: "transcriptions.client.api",
  mcp: "transcriptions.client.mcp",
};

export function clientLabel(job: Transcription, t: Translate): string {
  return t(CLIENTS[job.client] ?? "transcriptions.client.api");
}

export const canCancel = (job: Transcription) => job.state === "pending" || job.state === "running";
/** An upload keeps its copy until it is done, so a failed or cancelled one can run again. */
export const canRetry = (job: Transcription) => job.state === "failed" || job.state === "cancelled";
export const isDone = (job: Transcription) => job.state === "done";

export interface TranscriptionEvent {
  id: string;
  state: Transcription["state"] | "deleted";
  phase: Transcription["phase"];
  progress: number;
  eta_s?: number | null;
}

/**
 * A `transcription` event, applied to the list cache and nothing else: these arrive
 * every second while a file transcribes, and the global `invalidateQueries()` the other
 * events use would refetch the whole app each time.
 */
export function applyEvent(client: QueryClient, event: TranscriptionEvent): void {
  const list = client.getQueryData<{ transcriptions: Transcription[] }>(LIST_KEY);
  const known = list?.transcriptions.find((job) => job.id === event.id);
  if (event.state === "deleted") {
    if (list && known) {
      client.setQueryData(LIST_KEY, {
        transcriptions: list.transcriptions.filter((job) => job.id !== event.id),
      });
    }
    return;
  }
  if (!list || !known || known.state !== event.state) {
    // A new job, or a new state: positions, language and duration change with it.
    void client.invalidateQueries({ queryKey: LIST_KEY, exact: true });
    return;
  }
  client.setQueryData(LIST_KEY, {
    transcriptions: list.transcriptions.map((job) =>
      job.id === event.id
        ? { ...job, phase: event.phase, progress: event.progress, eta_s: event.eta_s ?? null }
        : job,
    ),
  });
}
