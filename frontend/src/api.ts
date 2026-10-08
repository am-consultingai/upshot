import type { UIMessage } from "ai";
import type { Language } from "./lib/transcribeAgain";
import type { LocaleFormats } from "./lib/timeFormat";
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
  /** What the user wrote about the meeting, and when they say it starts and ends. */
  description?: string | null;
  planned_start?: string | null;
  planned_end?: string | null;
  title_source?: string | null;
  /** Every calendar account the meeting is on (its dots). Empty: on no calendar. */
  calendar_accounts?: string[];
  /** Its calendar meeting is not settled: the user is asked which it was (D89). */
  needs_meeting?: boolean;
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
  /** File transcriptions that run before this meeting's waiting job (D86). */
  files_ahead?: number;
  evidence: { code: string; weight: number; detail: string }[];
  /** When the retention policy removed the raw audio. Null while it is still there. */
  audio_deleted_at?: string | null;
  calendar?: MeetingCalendar | null;
  action_items?: ActionItem[];
  /** A name for each transcript speaker slot ("THEM", "THEM_1"…) the user has named. */
  speaker_names?: Record<string, string>;
  chapters?: Chapter[];
  /** Which way the transcript runs, for the meeting's language (the backend's RTL list). */
  direction?: "ltr" | "rtl";
  /** Which way the notes run, for the summary's language. */
  summary_direction?: "ltr" | "rtl";
  /** The classifier's top guesses, for the hidden "Transcribe again as…" only. */
  language_candidates?: string[];
  /** Another recording of the same calendar meeting, not merged by itself (D89). */
  merge_with?: { id: string; title: string | null; started_at: string } | null;
}

/** One connected Google account (D82). Removed accounts are never listed. */
export interface CalendarAccount {
  id: string;
  address: string;
  /** "reconnect": its token stopped working and it needs signing in again. */
  state: "connected" | "reconnect";
  /** Off: everything of this account is hidden everywhere in Upshot. */
  visible: boolean;
  /** 1..6: which --account-N colour its dot is. */
  color: number;
  error: string | null;
  last_synced_at?: string | null;
  sync_error?: string | null;
  cached_events?: number;
}

/** The Google Calendar connections. Never carries a token. */
export interface CalendarStatus {
  /** False when this build has no Google OAuth client baked in. */
  configured: boolean;
  /** Across every account: "connected" when any is, "connecting" while a sign-in runs. */
  state: "disconnected" | "connecting" | "connected" | "reconnect";
  /** The first connected account's address. */
  account: string | null;
  accounts: CalendarAccount[];
  /** Google's consent page, while a connection is waiting on the browser. */
  auth_url: string | null;
  error: string | null;
  scope: string;
  last_synced_at?: string | null;
  sync_error?: string | null;
  cached_events?: number;
  /** The server opens Google's page itself (Windows); the page must not open a tab. */
  opens_externally?: boolean;
}

/** Which event: the account it is on, the calendar, and its id. */
export interface EventRef {
  account_id?: string;
  calendar_id: string;
  event_id: string;
}

/** A Google Calendar event from the local cache. Names only — never an address. */
export interface CalendarEvent {
  /** The account this copy was read from. */
  account_id: string;
  /** Every shown account the same meeting is on, when it is on more than one. */
  accounts?: string[];
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
  event?: {
    account_id?: string;
    calendar_id: string;
    event_id: string;
    start: string;
    end: string;
  };
  /** Every account the matched event is on. */
  accounts?: string[];
  title?: string | null;
  participants?: string[];
  participants_more?: number;
  conference_url?: string | null;
  private?: boolean;
  match?: {
    state: "matched" | "proposed" | "none";
    /** Who settled it: the calendar's matcher, the detector, or the user (D89). */
    source: "auto" | "detected" | "user";
    reason?: string;
  };
  candidates?: {
    account_id?: string;
    calendar_id: string;
    event_id: string;
    title: string | null;
    start: string;
  }[];
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

/** What Upshot is offering to record right now: a calendar meeting, or a detected call. */
export interface Prompt {
  kind: "calendar" | "detected";
  title: string;
  account_id?: string | null;
  calendar_id: string | null;
  event_id: string | null;
  conference_url: string | null;
  process: string | null;
  /** Calendar meetings booked at the same time that nothing told apart: pick one (D89). */
  candidates?: { account_id?: string | null; calendar_id: string; event_id: string; title: string }[];
  /** A detected call's score and the evidence behind it (app/detect/evidence.py codes). */
  score?: number | null;
  evidence?: { code: string; detail: string }[];
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
    /** The call's app let go: when the recording saves itself (D77). */
    ending: { ends_at: string; meeting_id: string } | null;
  };
  detector: { mode: string; state: string; decided?: boolean };
  /** The offer to record, shared with the toasts (app/prompts.py, D76). */
  prompt: Prompt | null;
  queue: Record<string, number>;
  queue_depth: number;
  disk_free_bytes: number;
  /** Everything under the data folder: recordings, transcripts, summaries. */
  storage_bytes?: number;
  fts: boolean;
  now: string;
  /** Which build answered (app/version.py). */
  build?: BuildInfo;
}

export interface BuildInfo {
  version: string;
  commit: string | null;
  built: string | null;
  frozen: boolean;
  /** This build can send crash reports and feedback (it carries a DSN, D87). */
  reports: boolean;
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
  /** False for a CLI that cannot be signed out from outside it (Antigravity, D79). */
  can_sign_out?: boolean;
  /** Sign out opens a window where the CLI's own /logout is typed (Antigravity, D79). */
  signout_in_window?: boolean;
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
 * `repo` is always the ivrit-ai turbo (D93): the same model on the GPU and the CPU.
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

/** A file transcription job, as `/api/v1/transcriptions` reports it (D86). */
export interface Transcription {
  id: string;
  state: "pending" | "running" | "done" | "failed" | "cancelled";
  source_name: string;
  source_kind: "upload" | "path";
  size_bytes: number | null;
  duration_s: number | null;
  options: { language: string; diarize: boolean; prompt: string; max_words_per_cue: number };
  client: "ui" | "api" | "mcp";
  language: string | null;
  language_conf: number | null;
  phase: "decode" | "check" | "language" | "transcribe" | "diarize" | null;
  progress: number;
  attempts: number;
  error: string | null;
  queued_at: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  position: number | null;
  waiting_reason: "recording" | "policy:when_idle" | "policy:scheduled" | "queue" | "retry" | null;
  /** Seconds left at this run's pace; null until the server has measured enough to say. */
  eta_s?: number | null;
  direction: "ltr" | "rtl";
  links: Record<string, string>;
}

/** One job running or waiting, meeting or file, from `/api/jobs/active`. */
export interface ActiveJob {
  kind: "meeting" | "file";
  /** The meeting's id, or the file transcription's. */
  id: string;
  title: string | null;
  client: "meeting" | "ui" | "api" | "mcp";
  /** The meeting stage; null for a file. */
  stage: string | null;
  state: "pending" | "running";
  position: number | null;
  waiting_reason: string | null;
  phase: string | null;
  /** 0–1; null where there is no honest figure (a summary, a stage that has not said). */
  progress: number | null;
  eta_s: number | null;
  queued_at: string;
  started_at: string | null;
  cancellable: boolean;
}

/** The stored result (`result.json`, version 1): the stable contract. */
export interface TranscriptionResult {
  version: number;
  id: string;
  source_name: string;
  duration_s: number;
  language: string | null;
  speakers: string[];
  diarized: boolean;
  segments: { id: number; start: number; end: number; speaker: string; text: string }[];
}

/** Settings → "Transcription for other apps": this install's commands, and which Claude is here. */
export interface TranscriptionConnect {
  enabled: boolean;
  api_url: string;
  install_dir: string | null;
  bridge: string | null;
  /** Claude Desktop from claude.ai ("classic"), from the Microsoft Store ("store"), or none. */
  claude_desktop: "classic" | "store" | null;
  /** Whether the `claude` command is on this computer. */
  claude_code: boolean;
  wsl_command: string | null;
  windows_command: string | null;
  curl_example: string;
  keep_days: number | null;
}

export function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)up_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/** What a failed request says, for a person: the server's ``detail`` without the status. */
export function reason(error: unknown): string {
  const text = String(error instanceof Error ? error.message : error).replace(/^\d+: /, "");
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // Not JSON: the text is the reason.
  }
  return text;
}

/** The Terms of Service as the app sees them (app/legal/terms.py, D83). */
export interface LegalMeta {
  version: string;
  effective: string;
  material: boolean;
  summary: string;
}

export interface LegalState {
  accepted_version: string | null;
  accepted_at: string | null;
  accepted_via: "installer" | "app" | null;
  current: LegalMeta;
  /** True: the Terms replace every other screen until they are accepted. */
  gate: boolean;
  notice: (LegalMeta & { kind: "changed" | "upcoming" }) | null;
  page: string;
}

/** Feedback from inside the app (D87): what the form sends. */
export interface FeedbackBody {
  kind: "idea" | "problem" | "praise" | "other";
  message: string;
  email: string;
  details: boolean;
  /** Send the screenshot taken with `takeScreenshot`, which the user has seen. */
  screenshot: boolean;
}

export interface FeedbackPreview {
  payload: Record<string, unknown>;
  attachments: { filename: string; bytes: number }[];
}

/** Crash reports (app/diagnostics, D87): can this build send, and what did the user say. */
export interface DiagnosticsState {
  /** False in a build from source: it carries no DSN and can send nothing. */
  available: boolean;
  consent: "unset" | "on" | "off";
  /** Exactly what was last sent, for anyone who wants to see it. */
  last_report: Record<string, unknown> | null;
}

/** The next version of the app, and how far it has got (app/updates, D87). */
export interface UpdateOffer {
  channel: "stable" | "beta";
  version: string;
  size: number;
  critical: boolean;
  /** Installs at the first safe moment whatever the setting: critical, or this copy is too old. */
  mandatory: boolean;
  min_version: string | null;
  published: string | null;
  notes: Partial<Record<"en" | "he", string>>;
  notes_url: string | null;
}

export interface UpdateState {
  phase: "idle" | "checking" | "downloading" | "waiting" | "ready" | "failed";
  current: string;
  channel: "stable" | "beta";
  auto_install: boolean;
  /** False when run from source: it says what is available but never installs. */
  enabled: boolean;
  available: UpdateOffer | null;
  held_back: string | null;
  progress: { bytes: number; total: number } | null;
  ready: boolean;
  last_checked_at: string | null;
  last_error: string | null;
  install?: {
    can_install: boolean;
    /** What a ready update waits for; null when nothing does. */
    waiting_for: "recording" | "call" | "jobs" | "meeting" | null;
    /** What the start after an update found. */
    last: { result: "updated" | "failed"; from: string; to: string } | null;
  };
}

export interface TermsDocument extends LegalMeta {
  title: string;
  html: string;
  sha256: string;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken());
    // A FormData body sets its own multipart Content-Type, boundary included; naming
    // one here would lose the boundary and the server could not read the parts.
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
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
  legal: () => request<LegalState>("/api/legal"),
  terms: (version?: string) =>
    request<TermsDocument>(`/api/legal/terms${version ? `?version=${encodeURIComponent(version)}` : ""}`),
  acceptTerms: (version: string) =>
    request<LegalState>("/api/legal/accept", { method: "POST", body: JSON.stringify({ version }) }),
  /** Not accepting quits the app; `quitting` is false where nothing can be asked to quit. */
  declineTerms: () => request<{ quitting: boolean }>("/api/legal/decline", { method: "POST" }),
  updates: () => request<UpdateState>("/api/updates"),
  diagnostics: () => request<DiagnosticsState>("/api/diagnostics"),
  feedback: () => request<{ available: boolean }>("/api/feedback"),
  feedbackPreview: (body: FeedbackBody) =>
    request<FeedbackPreview>("/api/feedback/preview", { method: "POST", body: JSON.stringify(body) }),
  sendFeedback: (body: FeedbackBody) =>
    request<{ reference: string; sent: boolean }>("/api/feedback", { method: "POST", body: JSON.stringify(body) }),
  /** Upshot's window, now, as an object URL to show; the server keeps the same picture. */
  takeScreenshot: async (): Promise<string> => {
    const response = await fetch("/api/feedback/screenshot", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken() },
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return URL.createObjectURL(await response.blob());
  },
  dropScreenshot: () => request<{ removed: boolean }>("/api/feedback/screenshot", { method: "DELETE" }),
  summaryRating: (meetingId: string) =>
    request<{ rating: { value: "up" | "down"; at: string } | null }>(
      `/api/feedback/summary/${encodeURIComponent(meetingId)}`,
    ),
  rateSummary: (meetingId: string, body: { rating: "up" | "down"; comment: string; include_summary: boolean }) =>
    request<{ reference: string | null; sent: boolean }>(`/api/feedback/summary/${encodeURIComponent(meetingId)}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** An error the page caught; the server reports it only with consent (D87, C5). */
  reportClientError: (body: { kind: string; message: string; stack: string }) =>
    request<{ reported: boolean }>("/api/diagnostics/client-error", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Answers at once; progress arrives as `updates` events. */
  checkUpdates: () => request<UpdateState>("/api/updates/check", { method: "POST" }),
  /** The app quits and the installer starts it again on the new version. */
  installUpdate: () => request<{ installing: string }>("/api/updates/install", { method: "POST" }),
  audioDevices: () => request<AudioDevices>("/api/audio/devices"),
  model: () => request<ModelStatus>("/api/model"),
  /** Starts the download in the background; a second call while one runs joins it. */
  modelDownload: () => request<ModelStatus>("/api/model/download", { method: "POST" }),
  modelCancel: () => request<ModelStatus>("/api/model/cancel", { method: "POST" }),
  deleteMeeting: (id: string) =>
    request<{ deleted: string }>(`/api/meetings/${id}`, { method: "DELETE" }),
  /** `account` narrows to those calendar accounts ("none": meetings on no calendar). */
  meetings: (params: Record<string, string> = {}, account?: string[]) => {
    const query = new URLSearchParams(params);
    for (const id of account ?? []) query.append("account", id);
    return request<{ meetings: Meeting[]; count: number }>(`/api/meetings?${query.toString()}`);
  },
  meeting: (id: string) => request<MeetingDetail>(`/api/meetings/${id}`),
  /** Windows' short date and time patterns (sShortDate, sShortTime). */
  locale: () => request<LocaleFormats>("/api/locale"),
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
  /** `language` (transcribe only): the hidden "Transcribe again as…"; implies force. */
  retry: (id: string, stage: string, force = false, language?: string) =>
    request<Job>(
      `/api/meetings/${id}/jobs/${stage}/retry?force=${force}` +
        (language ? `&language=${encodeURIComponent(language)}` : ""),
      { method: "POST" },
    ),
  languages: () => request<{ languages: Language[] }>("/api/languages"),
  /** With an event, the recording starts already matched to it ("Record this one"). */
  startRecording: (event?: EventRef) =>
    request<{ meeting_id: string }>("/api/recording/start", {
      method: "POST",
      body: JSON.stringify(event ?? {}),
    }),
  /** "Not a meeting" on the banner: withdrawn, and not offered again for it (D76). */
  dismissPrompt: () => request<{ prompt: null }>("/api/prompt/dismiss", { method: "POST" }),
  /** "Keep recording" while the call looks over (D77). */
  keepRecording: () =>
    request<{ meeting_id: string; kept: boolean }>("/api/recording/keep", { method: "POST" }),
  stopRecording: () =>
    request<{ meeting_id: string }>("/api/recording/stop", { method: "POST" }),
  /** Pause, or resume a paused recording: the one endpoint toggles (the tray's Pause). */
  togglePause: () => request<{ paused: boolean }>("/api/recording/pause", { method: "POST" }),
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
  llmSignin: (provider = "claude-subscription", background = false) =>
    request<{ launched: boolean; command: string; log?: string; url?: string }>(
      "/api/llm/signin",
      { method: "POST", body: JSON.stringify({ provider, background }) },
    ),
  /** The code Claude's page shows, passed to its windowless sign-in (D75). */
  llmSigninCode: (provider: string, code: string) =>
    request<{ sent: boolean; signed_in?: boolean }>("/api/llm/signin/code", {
      method: "POST",
      body: JSON.stringify({ provider, code }),
    }),
  /** Sign the CLI out with its own logout command. */
  llmSignout: (provider: string) =>
    request<{ signed_out: boolean; launched?: boolean }>("/api/llm/signout", {
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
  /** `background`: first-run setup's install, with no window and no sign-in in it (D75). */
  llmInstall: (provider = "claude-subscription", background = false) =>
    request<{ launched: boolean; command: string; docs: string; log?: string }>("/api/llm/install", {
      method: "POST",
      body: JSON.stringify({ provider, background }),
    }),
  calendarStatus: () => request<CalendarStatus>("/api/calendar/status"),
  calendarConnect: () =>
    request<CalendarStatus & { auth_url: string; opened: boolean }>("/api/calendar/connect", {
      method: "POST",
    }),
  /** Open a link in the user's default browser; `opened` false: open it here. */
  openLink: (url: string) =>
    request<{ opened: boolean }>("/api/open", { method: "POST", body: JSON.stringify({ url }) }),
  calendarCancel: () => request<CalendarStatus>("/api/calendar/cancel", { method: "POST" }),
  /** Sign in again to an account whose connection stopped working. */
  calendarReconnect: (id: string) =>
    request<CalendarStatus & { auth_url: string; opened: boolean }>(
      `/api/calendar/accounts/${id}/reconnect`,
      { method: "POST" },
    ),
  /** Show or hide an account: hidden, all of it is hidden everywhere (D82). */
  calendarSetVisible: (id: string, visible: boolean) =>
    request<CalendarStatus>(`/api/calendar/accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ visible }),
    }),
  /** Remove an account: revoked, and hidden for good until the same address connects. */
  calendarRemove: (id: string) =>
    request<CalendarStatus & { revoked: boolean; revoke_by_hand: string | null }>(
      `/api/calendar/accounts/${id}`,
      { method: "DELETE" },
    ),
  calendarEvents: (from: string, to: string) =>
    request<{ events: CalendarEvent[] }>(
      `/api/calendar/events?${new URLSearchParams({ from, to }).toString()}`,
    ),
  calendarSyncNow: () => request<CalendarStatus>("/api/calendar/sync", { method: "POST" }),
  meetingInvite: (id: string) =>
    request<{
      available: boolean;
      /** Why, machine-readably: "unmatched" and "no_connection" are the normal cases. */
      code?: "unmatched" | "no_connection" | "auth" | "offline" | "deleted" | "account_gone";
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
  /** Join two recordings of one calendar meeting; the earlier one is kept (D89). */
  mergeMeetings: (id: string, other: string) =>
    request<MeetingDetail>(`/api/meetings/${id}/merge`, {
      method: "POST",
      body: JSON.stringify({ other }),
    }),
  chooseMeetingEvent: (
    id: string,
    body: EventRef | { none: true },
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
  /** Everything running or waiting, meetings and files, in the order it will run. */
  activeJobs: () => request<{ jobs: ActiveJob[] }>("/api/jobs/active"),
  transcriptions: () =>
    request<{ transcriptions: Transcription[] }>("/api/v1/transcriptions?limit=200"),
  transcriptionResult: (id: string) =>
    request<TranscriptionResult>(`/api/v1/transcriptions/${encodeURIComponent(id)}/result?format=json`),
  transcriptionText: (id: string) =>
    request<string>(`/api/v1/transcriptions/${encodeURIComponent(id)}/result?format=txt&timestamps=true`),
  cancelTranscription: (id: string) =>
    request<Transcription>(`/api/v1/transcriptions/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  retryTranscription: (id: string) =>
    request<Transcription>(`/api/v1/transcriptions/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  deleteTranscription: (id: string) =>
    request<{ id: string; deleted: boolean }>(`/api/v1/transcriptions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  transcriptionConnect: () => request<TranscriptionConnect>("/api/transcription/connect"),
  addToClaudeDesktop: () =>
    request<{ opened: boolean }>("/api/transcription/add-to-claude-desktop", { method: "POST" }),
  /** The fallback: the extension copied to Downloads and shown in Explorer. */
  showClaudeExtension: () =>
    request<{ path: string }>("/api/transcription/show-claude-extension", { method: "POST" }),
  /** For a plain `<a href>` download: the page's cookie goes with it (D86). */
  transcriptionDownloadUrl: (id: string, format: string) =>
    `/api/v1/transcriptions/${encodeURIComponent(id)}/result?format=${format}`,
};
