/** The only place that talks to the backend. Cookie auth + the CSRF double-submit. */

export interface Meeting {
  id: string;
  title: string | null;
  state: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  language: string | null;
  summary_language: string | null;
  source: string;
  sensitive: number;
  folder: string;
  error: string | null;
  /** Present on the detail endpoint. A failed stage lives here, not in `error`. */
  jobs?: Job[];
}

export interface Job {
  stage: string;
  state: string;
  attempts: number;
  last_error: string | null;
  /** When the current attempt began. Null while pending. */
  started_at?: string | null;
}

export interface AudioTrack {
  seconds: number;
  /** 0..1. Zero means the track is digital silence — worth saying so in the UI. */
  peak: number;
  bytes: number;
}

export interface MeetingDetail extends Meeting {
  audio_tracks?: Record<string, AudioTrack>;
  jobs: Job[];
  evidence: { code: string; weight: number; detail: string }[];
  /** When the retention policy removed the raw audio. Null while it is still there. */
  audio_deleted_at?: string | null;
}

export interface Status {
  profile: string;
  policy: string;
  recorder: {
    active: boolean;
    armed: boolean;
    paused: boolean;
    meeting_id: string | null;
    levels: Record<string, number>;
  };
  detector: { mode: string; state: string };
  queue: Record<string, number>;
  queue_depth: number;
  disk_free_bytes: number;
  fts: boolean;
  now: string;
}

export interface LlmProvider {
  id: string;
  label: string;
  needs: string;
  ready: boolean;
  console?: string;
  detail?: string;
  /** Three-valued: null means the installed CLI is too old to be asked. */
  signed_in?: boolean | null;
  account?: string;
  /** The binary the server actually resolved, so hints can be pinned to it. */
  path?: string;
  can_install?: boolean;
  /** The exact line the Install button runs, shown before it is clicked. */
  install_command?: string;
  install_method?: string;
  install_docs?: string;
  update_hint?: string;
}

export interface AudioDevice {
  index: number;
  name: string;
  rate: number;
  channels: number;
  is_default: boolean;
}

export interface AudioDevices {
  devices: AudioDevice[];
  /** Playback endpoints; the loopback of the chosen one becomes the "them" track. */
  outputs: AudioDevice[];
  selected: number | null;
  selected_output: number | null;
  error: string | null;
  platform: string;
  capture: string;
  /** Preview streams opened this run. Climbing = something reopens the device in a loop. */
  meter_opens: number;
}

/** One reading from /api/audio/level. `rms` drives the bar, `peak` the hold marker. */
export interface AudioLevel {
  rms: number;
  peak: number;
  source: "monitor" | "recorder";
  clipped: boolean;
  error?: string;
  /** The server closed the stream on purpose; do not reconnect. */
  done?: boolean;
}

export interface DetectorEvent {
  id: number;
  at: string;
  process: string | null;
  window_title: string | null;
  peak_score: number;
  evidence: { code: string; weight: number; detail: string }[];
  outcome: string;
  meeting_id: string | null;
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)ma_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken());
    if (init.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  const type = response.headers.get("content-type") ?? "";
  return (
    type.includes("application/json") ? response.json() : response.text()
  ) as Promise<T>;
}

export const api = {
  status: () => request<Status>("/api/status"),
  audioDevices: () => request<AudioDevices>("/api/audio/devices"),
  deleteMeeting: (id: string) =>
    request<{ deleted: string }>(`/api/meetings/${id}`, { method: "DELETE" }),
  meetings: (params: Record<string, string> = {}) =>
    request<{ meetings: Meeting[]; count: number }>(
      `/api/meetings?${new URLSearchParams(params).toString()}`,
    ),
  meeting: (id: string) => request<MeetingDetail>(`/api/meetings/${id}`),
  summaryHtml: (id: string) =>
    request<string>(`/api/meetings/${id}/summary.html`),
  transcript: (id: string) =>
    request<{ segments: { start: number; speaker: string; text: string }[] }>(
      `/api/meetings/${id}/transcript`,
    ),
  /** Undo a discard: transcribe this recording after all. */
  keepMeeting: (id: string) =>
    request<{ id: string; state: string }>(`/api/meetings/${id}/keep`, { method: "POST" }),
  patchMeeting: (id: string, body: Record<string, unknown>) =>
    request<MeetingDetail>(`/api/meetings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  retry: (id: string, stage: string, force = false) =>
    request<Job>(`/api/meetings/${id}/jobs/${stage}/retry?force=${force}`, {
      method: "POST",
    }),
  startRecording: () =>
    request<{ meeting_id: string }>("/api/recording/start", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  stopRecording: () =>
    request<{ meeting_id: string }>("/api/recording/stop", { method: "POST" }),
  settings: () =>
    request<{ config: Record<string, unknown>; warnings: string[] }>(
      "/api/settings",
    ),
  putSettings: (values: Record<string, unknown>) =>
    request<{ config: Record<string, unknown>; warnings: string[] }>(
      "/api/settings",
      {
        method: "PUT",
        body: JSON.stringify({ values }),
      },
    ),
  llmStatus: () =>
    request<{ active: string; providers: LlmProvider[] }>("/api/llm/status"),
  secretStatus: () =>
    request<{ secrets: Record<string, boolean> }>("/api/settings/secrets"),
  putSecrets: (values: Record<string, string>) =>
    request<{ secrets: Record<string, boolean> }>("/api/settings/secrets", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  llmTest: (provider: string) =>
    request<{ provider: string; ok: boolean; model?: string; error?: string }>(
      "/api/llm/test",
      {
        method: "POST",
        body: JSON.stringify({ provider }),
      },
    ),
  llmSignin: () =>
    request<{ launched: boolean; command: string }>("/api/llm/signin", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  llmPrompt: () =>
    request<{ text: string; default: string; custom: boolean; version: string }>(
      "/api/llm/prompt",
    ),
  llmUpdate: () =>
    request<{ launched: boolean; command: string }>("/api/llm/update", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  llmInstall: () =>
    request<{ launched: boolean; command: string; docs: string }>("/api/llm/install", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  detectorEvents: () =>
    request<{ events: DetectorEvent[] }>("/api/detector/events?limit=50"),
  audioUrl: (id: string, track: string) =>
    `/api/meetings/${id}/audio?track=${track}`,
};
