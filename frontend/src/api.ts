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
}

export interface Job {
  stage: string;
  state: string;
  attempts: number;
  last_error: string | null;
}

export interface MeetingDetail extends Meeting {
  jobs: Job[];
  evidence: { code: string; weight: number; detail: string }[];
  review_reasons: string[];
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
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.text()) as Promise<T>;
}

export const api = {
  status: () => request<Status>("/api/status"),
  meetings: (params: Record<string, string> = {}) =>
    request<{ meetings: Meeting[]; count: number }>(
      `/api/meetings?${new URLSearchParams(params).toString()}`,
    ),
  meeting: (id: string) => request<MeetingDetail>(`/api/meetings/${id}`),
  summaryHtml: (id: string) => request<string>(`/api/meetings/${id}/summary.html`),
  transcript: (id: string) =>
    request<{ segments: { start: number; speaker: string; text: string }[] }>(
      `/api/meetings/${id}/transcript`,
    ),
  patchMeeting: (id: string, body: Record<string, unknown>) =>
    request<MeetingDetail>(`/api/meetings/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  retry: (id: string, stage: string) =>
    request<Job>(`/api/meetings/${id}/jobs/${stage}/retry`, { method: "POST" }),
  startRecording: () =>
    request<{ meeting_id: string }>("/api/recording/start", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  stopRecording: () => request<{ meeting_id: string }>("/api/recording/stop", { method: "POST" }),
  glossary: () =>
    request<{ terms: { term: string; aliases: string | null }[] }>("/api/glossary"),
  putGlossary: (terms: { term: string; aliases?: string | null }[]) =>
    request<{ terms: { term: string; aliases: string | null }[] }>("/api/glossary", {
      method: "PUT",
      body: JSON.stringify({ terms }),
    }),
  settings: () =>
    request<{ config: Record<string, unknown>; warnings: string[] }>("/api/settings"),
  putSettings: (values: Record<string, unknown>) =>
    request<{ config: Record<string, unknown>; warnings: string[] }>("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  llmStatus: () =>
    request<{ active: string; providers: LlmProvider[] }>("/api/llm/status"),
  secretStatus: () => request<{ secrets: Record<string, boolean> }>("/api/settings/secrets"),
  putSecrets: (values: Record<string, string>) =>
    request<{ secrets: Record<string, boolean> }>("/api/settings/secrets", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  llmTest: (provider: string) =>
    request<{ provider: string; ok: boolean; model?: string; error?: string }>("/api/llm/test", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  llmSignin: () =>
    request<{ launched: boolean; command: string }>("/api/llm/signin", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  detectorEvents: () => request<{ events: DetectorEvent[] }>("/api/detector/events?limit=50"),
  audioUrl: (id: string, track: string) => `/api/meetings/${id}/audio?track=${track}`,
};
