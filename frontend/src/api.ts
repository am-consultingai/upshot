import type { UIMessage } from "ai";
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
  /** Commitments this meeting recorded, and how many are still open. */
  actions_total?: number;
  actions_open?: number;
  /** Free labels the user gave it. */
  tags?: string[];
  /** The stage that failed, so a row can say "summary failed" rather than "failed". */
  failed_stage?: string | null;
}

/** A stretch of the conversation about one thing, as the summarizer divided it. */
export interface Chapter {
  title: string;
  start_ms: number;
  end_ms: number | null;
}

/** Why another meeting is related to this one. */
export type RelatedReason =
  | { code: "shared_actions"; count: number }
  | { code: "same_people"; names: string[] }
  | { code: "same_series" }
  | { code: "mentions"; term: string };

export interface RelatedMeeting {
  id: string;
  title: string | null;
  started_at: string;
  duration_s: number | null;
  reasons: RelatedReason[];
}

export interface AskAnswer {
  answer: string;
  citations: { meeting_id: string; at_ms: number; text?: string }[];
  scope: "meeting" | "related";
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

/**
 * One commitment a summary recorded, as data rather than as a sentence in the HTML.
 *
 * The model still writes whatever document it likes; it is now also asked to repeat
 * the action items inside it, which is what makes an inbox across meetings possible
 * at all. `done` is the user's and survives re-summarizing.
 */
export interface ActionItem {
  id: number;
  meeting_id: string;
  seq: number;
  who: string;
  what: string;
  due: string | null;
  /** The due date as a calendar date, resolved from `due` against the meeting's own date. */
  due_at: string | null;
  /** The lighter second line: why it matters, or what it blocks. */
  detail: string | null;
  /** Hidden from the inbox until this date. */
  snoozed_until: string | null;
  /** "user" for one added by hand, which a re-summarize keeps. */
  source: "model" | "user";
  at_ms: number | null;
  /** The owner resolved to the person running this recorder. */
  mine: boolean;
  done: boolean;
  done_at: string | null;
  meeting_title: string | null;
  meeting_started_at: string | null;
}

/** One transcript turn that matched a search, with the sentence it matched in. */
export interface SearchHit {
  meeting_id: string;
  meeting_title: string | null;
  meeting_started_at: string | null;
  speaker: string;
  /** The name the user gave that speaker slot on its meeting, when there is one. */
  speaker_name?: string | null;
  at_ms: number;
  text: string;
  /** The match with `[` `]` around the term, from SQLite's own snippet(). */
  snippet: string;
  /** Where it matched. Only a transcript hit has a speaker and a timestamp. */
  kind: "title" | "action" | "summary" | "transcript";
}

export interface MeetingDetail extends Meeting {
  audio_tracks?: Record<string, AudioTrack>;
  jobs: Job[];
  evidence: { code: string; weight: number; detail: string }[];
  /** When the retention policy removed the raw audio. Null while it is still there. */
  audio_deleted_at?: string | null;
  calendar?: MeetingCalendar | null;
  action_items?: ActionItem[];
  /** A name for each transcript speaker slot ("THEM", "THEM_1"…) the user has named. */
  speaker_names?: Record<string, string>;
  chapters?: Chapter[];
}

/** The Google Calendar connection. Never carries a token. */
export interface CalendarStatus {
  /** False when this build has no Google OAuth client baked in. */
  configured: boolean;
  state: "disconnected" | "connecting" | "connected" | "reconnect";
  account: string | null;
  /** Google's consent page, while a connection is waiting on the browser. */
  auth_url: string | null;
  error: string | null;
  scope: string;
  last_synced_at?: string | null;
  sync_error?: string | null;
  cached_events?: number;
}

/** A Google Calendar event from the local cache. Names only — never an address. */
export interface CalendarEvent {
  calendar_id: string;
  event_id: string;
  title: string | null;
  start: string;
  end: string;
  all_day: boolean;
  response: string | null;
  transparent: boolean;
  attendees: string[];
  attendees_partial: boolean;
  conference_url: string | null;
  /** The recording matched to this event, when there is one. */
  meeting_id?: string | null;
}

/** One person on the invitation. The only place an address appears in this app. */
export interface InvitePerson {
  name: string;
  email: string;
  optional: boolean;
  declined: boolean;
  organizer: boolean;
  self: boolean;
  response: string;
}

/** The live invitation behind a recording. Read from Google when shown; never stored. */
export interface Invite {
  title: string | null;
  start: string | null;
  end: string | null;
  location: string | null;
  organizer: string | null;
  attendees: string[];
  declined: string[];
  optional: string[];
  /** The organizer's description, as text, with each link's address kept inline. */
  agenda: string;
  links: string[];
  attachments: { title: string; url: string }[];
  conference_url: string | null;
  /** The event in Google Calendar. */
  html_link: string | null;
  /** Everyone invited, with their addresses. Shown on the meeting page and nowhere else. */
  people: InvitePerson[];
}

/** What a recording knows about its calendar event (a snapshot taken when matched). */
export interface MeetingCalendar {
  event?: { calendar_id: string; event_id: string; start: string; end: string };
  title?: string | null;
  participants?: string[];
  participants_more?: number;
  conference_url?: string | null;
  private?: boolean;
  match?: { state: "matched" | "proposed" | "none"; source: "auto" | "user"; reason?: string };
  candidates?: { calendar_id: string; event_id: string; title: string | null; start: string }[];
}

/**
 * The settings screen's whole payload.
 *
 * `pinned` maps a dotted config key to the environment variable holding it down. The
 * environment is the top configuration layer, so a launcher that exports one of these
 * beats `app_config.json` on every start: the control saves, reads back correctly, and is
 * overridden again the next time the app opens. Without this the screen had no way to
 * say so, and simply looked like it was forgetting the choice.
 */
export interface Settings {
  config: Record<string, unknown>;
  warnings: string[];
  pinned: Record<string, string>;
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
  detector: { mode: string; state: string; decided?: boolean };
  queue: Record<string, number>;
  queue_depth: number;
  disk_free_bytes: number;
  /** Everything under the data folder: recordings, transcripts, summaries. */
  storage_bytes?: number;
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
  /** The window Install or Sign in opened is still open, or a windowless sign-in runs. */
  console_open?: boolean;
  /** A windowless sign-in's link, while it waits for the browser. */
  signin_url?: string;
  /** What is left of a plan's allowance, when the CLI can say so cheaply. */
  quota?: string | null;
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

/**
 * The speech model, and where it will run: `/api/model`.
 *
 * `repo` is always ivrit-ai large-v3 (D60): the same model on the GPU and the CPU.
 */
export interface ModelStatus {
  repo: string;
  path: string;
  state: "missing" | "downloading" | "ready" | "failed" | "cancelled";
  done_bytes: number;
  total_bytes: number;
  /** The size before the download has started, when the hub has not been asked yet. */
  expected_bytes: number;
  free_bytes: number;
  /** Why a download failed, in the backend's words ("needs 2.6 GB free…"). */
  error: string;
  /** "no_space" when the drive is too full, so the screen words it itself. */
  code: "" | "no_space";
  device: "cpu" | "cuda";
  device_reason: "configured" | "no_cuda" | "low_vram" | "vram_unknown" | "gpu";
  vram_mb: number | null;
  min_vram_mb: number;
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

/** A conversation with the assistant, as the history lists it. */
export interface AssistantSession {
  id: string;
  title: string;
  provider: string;
  created_at: string;
  updated_at: string;
}

export function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)up_csrf=([^;]+)/);
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
  assistantStatus: () =>
    request<{ provider: string; available: boolean; problem: string | null }>("/api/assistant/status"),
  assistantSessions: () => request<{ sessions: AssistantSession[] }>("/api/assistant/sessions"),
  assistantSession: (id: string) =>
    request<{ session: AssistantSession; messages: UIMessage[] }>(`/api/assistant/sessions/${encodeURIComponent(id)}`),
  renameAssistantSession: (id: string, title: string) =>
    request<{ session: AssistantSession }>(`/api/assistant/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteAssistantSession: (id: string) =>
    request<{ deleted: string }>(`/api/assistant/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  status: () => request<Status>("/api/status"),
  audioDevices: () => request<AudioDevices>("/api/audio/devices"),
  model: () => request<ModelStatus>("/api/model"),
  /** Starts the download in the background; a second call while one runs joins it. */
  modelDownload: () => request<ModelStatus>("/api/model/download", { method: "POST" }),
  modelCancel: () => request<ModelStatus>("/api/model/cancel", { method: "POST" }),
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
    request<{ segments: { start: number; end?: number; speaker: string; text: string }[] }>(
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
  /** With an event, the recording starts already matched to it ("Record this one"). */
  startRecording: (event?: { calendar_id: string; event_id: string }) =>
    request<{ meeting_id: string }>("/api/recording/start", {
      method: "POST",
      body: JSON.stringify(event ?? {}),
    }),
  stopRecording: () =>
    request<{ meeting_id: string }>("/api/recording/stop", { method: "POST" }),
  settings: () => request<Settings>("/api/settings"),
  putSettings: (values: Record<string, unknown>) =>
    request<Settings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  llmStatus: () =>
    request<{ active: string; providers: LlmProvider[]; fallback?: string }>("/api/llm/status"),
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
  /** Which subscription CLI; the server defaults to Claude for callers that do not say. */
  llmSignin: (provider = "claude-subscription") =>
    request<{ launched: boolean; command: string; log?: string; url?: string }>(
      "/api/llm/signin",
      { method: "POST", body: JSON.stringify({ provider }) },
    ),
  /** Sign the CLI out with its own logout command. */
  llmSignout: (provider: string) =>
    request<{ signed_out: boolean }>("/api/llm/signout", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  /** Stop a sign-in running with no window; it holds a port until it is stopped. */
  llmSigninCancel: (provider: string) =>
    request<{ stopped: boolean }>("/api/llm/signin/cancel", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  llmPrompt: () =>
    request<{ text: string; default: string; custom: boolean; version: string }>(
      "/api/llm/prompt",
    ),
  /** Which subscription CLI; the server defaults to Claude for callers that do not say. */
  llmUpdate: (provider = "claude-subscription") =>
    request<{ launched: boolean; command: string }>("/api/llm/update", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  llmInstall: (provider = "claude-subscription") =>
    request<{ launched: boolean; command: string; docs: string; log?: string }>("/api/llm/install", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  calendarStatus: () => request<CalendarStatus>("/api/calendar/status"),
  calendarConnect: () =>
    request<CalendarStatus & { auth_url: string }>("/api/calendar/connect", { method: "POST" }),
  calendarCancel: () => request<CalendarStatus>("/api/calendar/cancel", { method: "POST" }),
  calendarDisconnect: () =>
    request<CalendarStatus & { revoked: boolean; revoke_by_hand: string | null }>(
      "/api/calendar/disconnect",
      { method: "POST" },
    ),
  calendarEvents: (from: string, to: string) =>
    request<{ events: CalendarEvent[] }>(
      `/api/calendar/events?${new URLSearchParams({ from, to }).toString()}`,
    ),
  calendarSyncNow: () => request<CalendarStatus>("/api/calendar/sync", { method: "POST" }),
  calendarForget: () =>
    request<CalendarStatus & { deleted: number }>("/api/calendar/cache", { method: "DELETE" }),
  meetingInvite: (id: string) =>
    request<{
      available: boolean;
      /** Why, machine-readably: "unmatched" and "no_connection" are the normal cases. */
      code?: "unmatched" | "no_connection" | "auth" | "offline" | "deleted";
      reason?: string;
      reconnect?: boolean;
      invite?: Invite;
    }>(
      `/api/meetings/${id}/invite`,
    ),
  meetingCalendar: (id: string) =>
    request<{ calendar: MeetingCalendar | null; candidates: CalendarEvent[] }>(
      `/api/meetings/${id}/calendar`,
    ),
  chooseMeetingEvent: (
    id: string,
    body: { calendar_id: string; event_id: string } | { none: true },
  ) =>
    request<MeetingDetail>(`/api/meetings/${id}/calendar`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  actionItems: (params: Record<string, string> = {}) =>
    request<{ items: ActionItem[]; count: number; open: number }>(
      `/api/action-items?${new URLSearchParams(params).toString()}`,
    ),
  setActionDone: (id: number, done: boolean) =>
    request<ActionItem>(`/api/action-items/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ done }),
    }),
  patchActionItem: (
    id: number,
    body: Partial<{
      done: boolean;
      due_at: string | null;
      snoozed_until: string | null;
      who: string;
      what: string;
      detail: string | null;
    }>,
  ) =>
    request<ActionItem>(`/api/action-items/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  addActionItem: (
    meetingId: string,
    body: { what: string; who?: string; due_at?: string | null; detail?: string | null },
  ) =>
    request<ActionItem>(`/api/meetings/${meetingId}/action-items`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteActionItem: (id: number) =>
    request<{ deleted: number }>(`/api/action-items/${id}`, { method: "DELETE" }),
  tags: () => request<{ tags: { tag: string; count: number }[] }>("/api/tags"),
  putTags: (meetingId: string, tags: string[]) =>
    request<{ tags: string[] }>(`/api/meetings/${meetingId}/tags`, {
      method: "PUT",
      body: JSON.stringify({ tags }),
    }),
  related: (meetingId: string) =>
    request<{ related: RelatedMeeting[] }>(`/api/meetings/${meetingId}/related`),
  ask: (meetingId: string, question: string, scope: "meeting" | "related") =>
    request<AskAnswer>(`/api/meetings/${meetingId}/ask`, {
      method: "POST",
      body: JSON.stringify({ question, scope }),
    }),
  search: (q: string) =>
    request<{ q: string; hits: SearchHit[]; count: number }>(
      `/api/search?${new URLSearchParams({ q }).toString()}`,
    ),
  /** Stages that failed or are deliberately waiting (an allowance, a sign-in), in words. */
  attention: () =>
    request<{
      items: {
        meeting_id: string;
        title: string | null;
        stage: string;
        state: "failed" | "waiting";
        message: string;
        retry_at: string | null;
      }[];
    }>("/api/attention"),
  audioUrl: (id: string, track: string) =>
    `/api/meetings/${id}/audio?track=${track}`,
};
