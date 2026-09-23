import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Job } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import { formatElapsed, formatShortDate } from "../lib/format";
import MeetingCalendarCard from "../components/MeetingCalendarCard";
import MeetingDetailsDialog from "../components/MeetingDetailsDialog";
import MeetingRail from "../components/MeetingRail";
import MeetingChips from "../components/MeetingChips";
import Menu from "../components/Menu";
import Tooltip from "../components/Tooltip";
import StateBadge from "../components/StateBadge";
import { Spinner } from "../components/BusyButton";
import AudioPlayer, { type AudioPlayerHandle, type Band } from "../components/AudioPlayer";
import ActionItemsBlock from "../components/ActionItemsBlock";
import SummaryMinimap from "../components/SummaryMinimap";
import Transcript from "../components/Transcript";
import { confirmDialog } from "../components/ConfirmDialog";
import { toast } from "../components/Toaster";
import { speakers } from "../lib/speakers";
import { markdownFilename, summaryToMarkdown } from "../lib/markdown";
import { leadFirst } from "../lib/summary";

function contentDirection(language: string | null): "rtl" | "ltr" {
  return language === "he" ? "rtl" : "ltr";
}

/** What each stage is doing, in words. "summarize: running" told nobody anything. */
const STAGE_LABEL: Record<string, MessageKey> = {
  transcribe: "meeting.stageTranscribe",
  assemble: "meeting.stageAssemble",
  summarize: "meeting.stageSummarize",
  render: "meeting.stageRender",
  deliver: "meeting.stageDeliver",
};

/**
 * What a failed stage says in the user's terms.
 *
 * "summarize failed after attempts (0)" told the reader nothing they could use: not
 * what "summarize" is as a noun, not whether the recording survived, not whether
 * pressing the button costs money or five seconds. The technical detail is still
 * here — it is just no longer the whole message.
 */
const FAILED_WHILE: Record<string, MessageKey> = {
  transcribe: "meeting.failedWhileTranscribe",
  assemble: "meeting.failedWhileAssemble",
  summarize: "meeting.failedWhileSummarize",
  render: "meeting.failedWhileRender",
  deliver: "meeting.failedWhileDeliver",
};

/** The stage actually working, else the first one waiting. */
function currentJob(jobs: Job[]): Job | undefined {
  return jobs.find((job) => job.state === "running") ?? jobs.find((job) => job.state === "pending");
}

export default function MeetingPage() {
  const { id = "" } = useParams();
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const playerRef = useRef<AudioPlayerHandle | null>(null);
  // Where playback is now, so the transcript can show which line is being spoken.
  const [playhead, setPlayhead] = useState(0);
  // Polled while the pipeline is working, so a queued stage visibly finishes — or
  // visibly fails — instead of leaving the page on a hopeful message forever.
  const [pipelineBusy, setPipelineBusy] = useState(false);
  /*
   * Which of the two pills is showing. Not in the URL: it is a reading position
   * inside one meeting, not a place, and putting it in history would mean the back
   * button walked through tab flips instead of leaving the meeting.
   */
  const [tab, setTab] = useState<"summary" | "transcript">("summary");
  const [details, setDetails] = useState(false);
  const [addRequest, setAddRequest] = useState<{ text: string; at: number } | null>(null);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null);

  // Always polled, quickly while the pipeline is working. Freshness used to depend on a
  // flag set by the button press, so a page opened mid-run — or one whose flag had gone
  // stale — sat on job data that never corrected itself and kept reporting "running"
  // long after the work had finished.
  const meeting = useQuery({
    queryKey: ["meeting", id],
    queryFn: () => api.meeting(id),
    refetchInterval: pipelineBusy ? 2000 : 8000,
  });
  const summary = useQuery({
    queryKey: ["summary", id],
    queryFn: () => api.summaryHtml(id),
    // `retry: false` because a meeting with no summary yet legitimately 404s. But that
    // also means the answer sticks: the pipeline finished, summary.html was written, and
    // the page went on showing "no summary yet" until it was reloaded by hand.
    retry: false,
    refetchInterval: pipelineBusy ? 2000 : false,
  });
  const transcript = useQuery({
    queryKey: ["transcript", id],
    queryFn: () => api.transcript(id),
    retry: false,
  });

  /*
   * Arrived from a search hit: play from the moment that matched.
   *
   * Waits on the transcript rather than firing on mount, because the player is only
   * rendered once the meeting has audio, and seeking a <audio> that is not in the
   * document yet does nothing at all. Done once per `at`, so scrubbing afterwards
   * is not yanked back by a re-render.
   */
  const [params, setParams] = useSearchParams();
  const at = params.get("at");
  const seeked = useRef<string | null>(null);
  /*
   * A seek asked for from anywhere on the page — a chapter, a citation, a search hit.
   * The player lives on the transcript tab, so the tab switches first and the seek is
   * carried out once the player is there to take it.
   */
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  const seekTo = (seconds: number) => {
    setTab("transcript");
    setPendingSeek(seconds);
    setPlayhead(seconds);
  };
  useEffect(() => {
    if (!at || seeked.current === at || !transcript.data) return;
    const seconds = Number(at) / 1000;
    if (!Number.isFinite(seconds)) return;
    seeked.current = at;
    seekTo(seconds);
  }, [at, transcript.data]);
  useEffect(() => {
    if (pendingSeek === null) return;
    const timer = window.setTimeout(() => {
      playerRef.current?.seek(pendingSeek);
      const turns = [...document.querySelectorAll<HTMLElement>("[data-testid=transcript-turn]")];
      const reached = (node: HTMLElement) => pendingSeek * 1000 + 1 - Number(node.dataset.atMs) >= 0;
      const target = turns.filter(reached).pop();
      target?.scrollIntoView({ block: "center", behavior: "smooth" });
      setPendingSeek(null);
    }, 60);
    return () => window.clearTimeout(timer);
  }, [pendingSeek]);
  // Summarizing is the one pipeline stage worth running on demand: it is the only one
  // whose output you might want again after editing the prompt, and re-running it costs
  // tokens, so it stays a deliberate press rather than anything automatic.
  // When Summarize was last pressed. Until a meeting fetch newer than the press arrives,
  // the jobs on screen predate it and show nothing running — the spinner would blink off
  // between the click and the first poll.
  const [pressedAt, setPressedAt] = useState<number | null>(null);
  const summarize = useMutation({
    // force: pressing this means redo it, not "redo it if you think it is stale".
    mutationFn: () => api.retry(id, "summarize", true),
    onSuccess: () => {
      setPressedAt(Date.now());
      setPipelineBusy(true);
      queryClient.invalidateQueries();
    },
  });

  // A stage that failed five times used to look exactly like one still running: the page
  // said "Queued" and never spoke again. Whatever the queue knows, the page now shows.
  const jobs = meeting.data?.jobs ?? [];
  const running = jobs.filter((job) => job.state === "pending" || job.state === "running");
  const failed = jobs.filter((job) => job.state === "failed");
  const current = currentJob(jobs);
  const awaitingFirstPoll = pressedAt !== null && meeting.dataUpdatedAt < pressedAt;
  // A stage held back on purpose — an allowance used up, a sign-in needed — as the server
  // words it. A failure being retried is not one of these and is not explained here.
  const attention = useQuery({
    queryKey: ["attention"],
    queryFn: api.attention,
    enabled: running.length > 0,
    refetchInterval: running.length > 0 ? 5000 : false,
  });
  const waiting = (attention.data?.items ?? []).find(
    (item) => item.meeting_id === id && item.state === "waiting",
  );
  const working = summarize.isPending || awaitingFirstPoll || running.length > 0;

  // Ticks only while something is working, for the elapsed time beside the spinner.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!working) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [working]);
  useEffect(() => {
    if (running.length > 0 && !pipelineBusy) {
      setPipelineBusy(true);
      return;
    }
    if (running.length === 0 && pipelineBusy) {
      setPipelineBusy(false);
      // The rendered file usually lands on the very tick the last stage finishes, so the
      // summary is fetched once more here rather than left to the next idle poll.
      queryClient.invalidateQueries({ queryKey: ["summary", id] });
      queryClient.invalidateQueries({ queryKey: ["meetings"] });
    }
  }, [running.length, pipelineBusy, queryClient, id]);
  const keep = useMutation({
    mutationFn: () => api.keepMeeting(id),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const rename = useMutation({
    mutationFn: (title: string) => api.patchMeeting(id, { title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["meeting", id] });
      // The list and the calendar show the title too.
      queryClient.invalidateQueries({ queryKey: ["meetings"] });
    },
  });
  /** The title being typed, or null when the heading is not being edited. */
  const [draft, setDraft] = useState<string | null>(null);
  // Arrived from the sidebar's "Rename": open straight into the field.
  useEffect(() => {
    if (params.get("rename") !== "1" || !meeting.data) return;
    setDraft(meeting.data.title ?? "");
    const next = new URLSearchParams(params);
    next.delete("rename");
    setParams(next, { replace: true });
  }, [params, setParams, meeting.data]);
  const findRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "f") return;
      event.preventDefault();
      setTab("transcript");
      window.setTimeout(() => findRef.current?.focus(), 0);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  /*
   * Escape has to beat the blur that follows it. Removing the focused field makes the
   * browser fire blur as it goes, so without this the cancel path saved the very text
   * it was cancelling.
   */
  const cancelled = useRef(false);
  const commitRename = () => {
    const next = draft?.trim() ?? "";
    setDraft(null);
    if (cancelled.current) {
      cancelled.current = false;
      return;
    }
    // Empty would erase the name, and unchanged is not an edit: neither is sent.
    if (next && next !== (meeting.data?.title ?? "")) rename.mutate(next);
  };

  const [copied, setCopied] = useState(false);
  const copySummary = async (html: string) => {
    // HTML first: this summary is written to be pasted into mail, and plain text would
    // throw away the layout the prompt spent its tokens producing. Plain text goes on
    // the clipboard too, for anywhere that cannot take the rich flavour.
    const plain = new DOMParser().parseFromString(html, "text/html").body.textContent ?? "";
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([plain], { type: "text/plain" }),
        }),
      ]);
    } catch {
      await navigator.clipboard.writeText(plain);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const exportMarkdown = (html: string) => {
    const text = summaryToMarkdown(html, meeting.data?.title ?? null);
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = markdownFilename(meeting.data?.title ?? null, id);
    link.click();
    // Revoked on the next tick: Safari needs the object alive when the click is handled.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const segments = transcript.data?.segments ?? [];
  const remove = useMutation({
    mutationFn: () => api.deleteMeeting(id),
    onSuccess: () => {
      toast({ title: t("toast.deleted").replace("{title}", meeting.data?.title ?? id) });
      void queryClient.invalidateQueries();
      navigate("/");
    },
  });
  const askToDelete = async () => {
    const title = meeting.data?.title ?? id;
    const yes = await confirmDialog({
      title: t("meeting.deleteTitle"),
      body: (
        <>
          {t("meeting.deleteBodyBefore")} <b className="font-semibold text-primary">{title}</b>{" "}
          {t("meeting.deleteBodyAfter")}
        </>
      ),
      confirm: t("meeting.deletePermanently"),
      cancel: t("common.cancel"),
    });
    if (yes) remove.mutate();
  };

  const dir = useMemo(
    () =>
      contentDirection(
        meeting.data?.summary_language ?? meeting.data?.language ?? null,
      ),
    [meeting.data],
  );

  /*
   * The line being spoken: the last one that has started. Segments carry a start
   * and no end, so "current" is a boundary search rather than a range test, and a
   * gap between turns belongs to the turn before it rather than to nothing.
   *
   * This completes the loop the summary begins. A generated claim leads to the
   * transcript passage behind it, that passage leads to the audio, and the audio
   * leads back to the line being spoken — so a summary anyone doubts can be
   * checked all the way down, which is the thing this app can do and the products
   * it is measured against cannot, because they keep no recording.
   */
  const spokenIndex = useMemo(() => {
    const segments = transcript.data?.segments ?? [];
    if (segments.length === 0 || playhead <= 0) return -1;
    let found = -1;
    for (let index = 0; index < segments.length; index += 1) {
      if (segments[index].start <= playhead) found = index;
      else break;
    }
    return found;
  }, [transcript.data, playhead]);

  if (meeting.isLoading)
    return <p data-testid="loading">{t("common.loading")}</p>;
  if (!meeting.data) return <p data-testid="error">{t("common.error")}</p>;

  // Press play, hear the meeting. The server mixes both sides into one stream as it is
  // read; the per-track URLs still exist for diagnosis but the UI does not offer them —
  // nobody wants to choose a track before listening to their own meeting.
  const hasAudio = Object.keys(meeting.data.audio_tracks ?? {}).length > 0;
  const people = speakers(segments, meeting.data.speaker_names ?? {}, meeting.data.calendar?.participants ?? [], {
    you: t("meeting.you"),
    them: t("meeting.themSaid"),
    speaker: t("meeting.speakerN"),
  });
  const colourOf = new Map(people.map((person) => [person.slot, person]));
  const bands: Band[] = segments.map((segment, index) => ({
    start: segment.start,
    end: segment.end ?? segments[index + 1]?.start ?? segment.start + 4,
    colour: colourOf.get(segment.speaker)?.colour ?? "var(--border-strong)",
    name: colourOf.get(segment.speaker)?.name ?? segment.speaker,
  }));
  const matchState = meeting.data.calendar?.match?.state;

  return (
    <section data-testid="meeting-page" data-meeting-id={id} className="flex h-full min-h-0 flex-col">
      <div
        data-testid="meeting-bar"
        className="flex h-11 flex-none items-center gap-2 border-b border-line-subtle px-5"
      >
        <Link to="/" className="text-sm text-tertiary hover:text-primary">
          {t("nav.timeline")}
        </Link>
        <span className="text-sm text-tertiary opacity-50">/</span>
        <span className="truncate text-sm text-secondary">
          <bdi>{tab === "summary" ? formatShortDate(meeting.data.started_at, locale) : (meeting.data.title ?? id)}</bdi>
        </span>
        <span className="ms-auto" />
        {meeting.data.calendar?.conference_url && (
          <a
            data-testid="meeting-join-bar"
            href={meeting.data.calendar.conference_url}
            target="_blank"
            rel="noreferrer"
            className="h-7 rounded-md px-2.5 text-xs leading-7 text-secondary shadow-[var(--shadow-ring)] hover:bg-a-200 hover:text-primary"
          >
            {t("calendar.joinMeeting")}
          </a>
        )}
        <StateBadge state={meeting.data.state} />
        {/*
         * One primary action per view. Beside the summary it is copying it — the
         * summary is written to be pasted into mail. Beside the transcript it is
         * exporting, as a ghost, because the transcript is read here, not sent.
         */}
        {summary.data && tab === "summary" && (
          <Tooltip label={t("meeting.copy")} hint={t("help.copySummary")}>
          <button
            type="button"
            data-testid="copy-summary-bar"
            onClick={() => copySummary(summary.data as string)}
            className="h-7 rounded-md bg-accent px-2.5 text-xs font-medium text-on-accent shadow-[var(--shadow-sm),var(--shadow-edge)] hover:brightness-110 active:translate-y-px"
          >
            {copied ? t("meeting.copied") : t("meeting.copy")}
          </button>
          </Tooltip>
        )}
        {summary.data && tab === "transcript" && (
          <Tooltip label={t("meeting.export")} hint={t("help.export")}>
          <button
            type="button"
            data-testid="export-bar"
            onClick={() => exportMarkdown(summary.data as string)}
            className="h-7 rounded-md px-2.5 text-xs font-medium text-primary shadow-[var(--shadow-ring)] hover:bg-a-200 active:bg-a-300"
          >
            {t("meeting.export")}
          </button>
          </Tooltip>
        )}
        <Menu
          label={t("meeting.more")}
          items={[
            {
              id: "resummarize",
              label: summary.data ? t("meeting.resummarize") : t("meeting.summarize"),
              run: () => summarize.mutate(),
            },
            { id: "prompt", label: t("meeting.viewPrompt"), run: () => navigate("/settings#prompt") },
            ...(summary.data
              ? [
                  {
                    id: "markdown",
                    label: t("meeting.exportMarkdown"),
                    run: () => exportMarkdown(summary.data as string),
                  },
                ]
              : []),
            { id: "rename", label: t("meeting.rename"), run: () => setDraft(meeting.data?.title ?? "") },
            { id: "calendar", label: t("meeting.calendarEvent"), run: () => setDetails(true) },
            {
              id: "delete",
              label: t("meeting.deleteEllipsis"),
              danger: true,
              separated: true,
              run: () => void askToDelete(),
            },
          ]}
        />
      </div>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div ref={setScroller} data-meeting-scroller className="min-w-0 flex-1 overflow-y-auto">
          {/*
           * A reading measure, not the width of the window. Every application in this
           * category caps it and they land within sixty pixels of each other —
           * Granola 640, Reflect 672, Obsidian 700 — because prose stops being
           * readable somewhere past 80 characters a line. The minimap sits in the
           * margin beside it, where it costs no reading width.
           */}
          <div className="flex gap-6 px-8 pt-7">
            <div className="w-full min-w-0 max-w-[37rem]">
              <header className="mb-3">
                {draft === null ? (
                  <h1
                    className="display cursor-text text-[2rem]"
                    data-testid="meeting-title"
                    title={t("meeting.rename")}
                    onDoubleClick={() => setDraft(meeting.data?.title ?? "")}
                  >
                    {rename.isPending ? rename.variables : (meeting.data.title ?? id)}
                  </h1>
                ) : (
                  /*
                   * The heading becomes the field, at the heading's size, so renaming
                   * reads as editing the name in place rather than filling in a form.
                   * Enter or leaving the field saves; Escape puts the old name back.
                   */
                  <input
                    data-testid="meeting-title-input"
                    aria-label={t("meeting.rename")}
                    autoFocus
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onFocus={(event) => event.currentTarget.select()}
                    onBlur={commitRename}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur();
                      if (event.key === "Escape") {
                        cancelled.current = true;
                        setDraft(null);
                      }
                    }}
                    className="display w-full min-w-0 rounded-md bg-surface-2 px-2 py-0.5 text-[2rem]"
                  />
                )}
              </header>

              <MeetingChips meeting={meeting.data} onPeople={() => setDetails(true)} />

              {/*
               * A proposed match is a question only the user can answer, so it stays on
               * the page until they do. A settled one lives behind the people chip.
               */}
              {matchState === "proposed" && (
                <MeetingCalendarCard meetingId={id} calendar={meeting.data.calendar} />
              )}

              {/*
               * Summary and transcript as two pills, not a stack. Circleback ships
               * exactly this pair and Otter the same; Fathom's five peer tabs scan
               * measurably worse.
               */}
              <div
                data-testid="meeting-tabs"
                className="mb-6 flex w-max gap-0.5 rounded-md bg-surface-3 p-0.5"
                role="tablist"
              >
                {(["summary", "transcript"] as const).map((value) => (
                  <Tooltip
                    key={value}
                    label={t(value === "summary" ? "meeting.summary" : "meeting.transcript")}
                    hint={t(value === "summary" ? "help.tabSummary" : "help.tabTranscript")}
                  >
                  <button
                    type="button"
                    role="tab"
                    data-testid={`meeting-tab-${value}`}
                    aria-pressed={tab === value}
                    aria-selected={tab === value}
                    onClick={() => setTab(value)}
                    className={`h-6.5 rounded-sm px-3.5 text-sm ${
                      tab === value
                        ? "bg-raised text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]"
                        : "text-secondary hover:bg-a-200 hover:text-primary"
                    }`}
                  >
                    {t(value === "summary" ? "meeting.summary" : "meeting.transcript")}
                  </button>
                  </Tooltip>
                ))}
              </div>

              {meeting.data.evidence.length > 0 && tab === "summary" && (
                <p className="mb-4 text-sm text-secondary" data-testid="recorded-because">
                  {t("meeting.recordedBecause")}:{" "}
                  {meeting.data.evidence.map((item) => item.detail).join(", ")}
                </p>
              )}

              {meeting.data.state === "DISCARDED" && (
                /*
                 * A recording shorter than the minimum is filed rather than transcribed.
                 * Nothing was deleted — the audio is on disk and the state machine has
                 * always allowed the way back — so the page offers it.
                 */
                <div
                  data-testid="discarded-notice"
                  className="mb-5 flex flex-wrap items-center gap-3 rounded-lg bg-surface-2 px-3 py-2.5 text-sm"
                >
                  <span className="text-secondary">{t("meeting.discardedShort")}</span>
                  <button
                    type="button"
                    data-testid="keep-meeting"
                    aria-busy={keep.isPending}
                    onClick={() => keep.mutate()}
                    className="ms-auto rounded-sm bg-accent px-2.5 py-1 text-xs font-medium text-on-accent"
                  >
                    {t("meeting.keepAnyway")}
                  </button>
                </div>
              )}

              {tab === "transcript" &&
                (meeting.data.audio_deleted_at ? (
                  // Deleted on purpose, after the retention period. Saying "no audio"
                  // here would read as a failed recording.
                  <p data-testid="audio-deleted" className="mb-4 text-sm text-secondary">
                    {t("meeting.audioDeleted")}
                  </p>
                ) : !hasAudio ? (
                  <p data-testid="no-audio" className="mb-4 text-sm text-secondary">
                    {t("meeting.noAudio")}
                  </p>
                ) : null)}

              {/*
               * The commitments this summary recorded, as objects rather than as
               * sentences inside the HTML. Above the summary, not below it: they are the
               * only part of this page anyone acts on.
               */}
              {tab === "summary" && (summary.data || (meeting.data.action_items?.length ?? 0) > 0) && (
                <ActionItemsBlock
                  meetingId={id}
                  items={meeting.data.action_items ?? []}
                  addRequest={addRequest}
                />
              )}

              {working && (
                <p
                  className="mb-3 inline-flex items-center gap-1.5 text-xs text-secondary"
                  data-testid="stage-running"
                  data-stage={current?.stage ?? ""}
                  data-state={current?.state ?? ""}
                  role="status"
                >
                  <Spinner className="text-accent" />
                  {current?.state === "running"
                    ? `${t(STAGE_LABEL[current.stage] ?? "meeting.stageWorking")}…`
                    : `${t("meeting.stageWaiting")}: ${t(
                        STAGE_LABEL[current?.stage ?? "summarize"] ?? "meeting.stageWorking",
                      )}`}
                  {current?.state === "running" && current.started_at && (
                    <span className="tabular-nums text-tertiary" data-testid="stage-elapsed">
                      {formatElapsed(current.started_at, now)}
                    </span>
                  )}
                </p>
              )}
              {/*
               * Why a stage is waiting rather than running, in the provider's own plain
               * words — "your ChatGPT plan's Codex allowance is used up; it resets at …".
               * A waiting job is not a failed one, and without this line it looked like
               * one that had silently stalled.
               */}
              {waiting && (
                <p data-testid="stage-waiting-reason" className="mb-3 max-w-prose text-xs text-secondary">
                  {waiting.message}
                </p>
              )}

              {failed.length > 0 && (
                <div data-testid="stage-failed" className="mb-4 rounded-lg bg-danger-quiet p-3 text-sm">
                  <p className="font-medium text-danger">{t("meeting.failedLead")}</p>
                  {failed.map((job) => (
                    <p key={job.stage} data-testid="failed-stage" data-stage={job.stage} className="text-danger">
                      {t(FAILED_WHILE[job.stage] ?? "meeting.failedWhileWorking")}
                    </p>
                  ))}
                  {/* The two questions the reader actually has, answered before the trace. */}
                  <p className="mt-1.5 text-secondary">{t("meeting.failedSafe")}</p>
                  <p className="text-secondary">{t("meeting.failedRetryHere")}</p>
                  {failed.some((job) => job.last_error) && (
                    <details data-testid="failed-detail" className="mt-2">
                      <summary className="cursor-pointer text-xs text-tertiary">{t("meeting.failedDetail")}</summary>
                      {failed.map((job) => (
                        <p key={job.stage} className="mt-1 font-mono text-2xs text-tertiary">
                          {job.stage} ({job.attempts}){job.last_error ? `: ${job.last_error}` : ""}
                        </p>
                      ))}
                    </details>
                  )}
                  <button
                    type="button"
                    data-testid="retry-summarize"
                    onClick={() => summarize.mutate()}
                    className="mt-2.5 h-7 rounded-md bg-raised px-2.5 text-xs font-medium text-primary shadow-[var(--shadow-ring)] hover:bg-a-200"
                  >
                    {t("meeting.summarize")}
                  </button>
                </div>
              )}

              {tab === "summary" &&
                (summary.data ? (
                  <div
                    ref={summaryRef}
                    data-testid="summary-html"
                    dir={dir}
                    className="summary-prose mb-12"
                    /*
                     * A next step the summary wrote under a decision files itself as an
                     * action item: the add row opens with its words in it, one Enter
                     * from being on the list.
                     */
                    onClick={(event) => {
                      const next = (event.target as HTMLElement).closest(".next");
                      if (!next) return;
                      setAddRequest({ text: next.textContent?.trim() ?? "", at: Date.now() });
                      window.setTimeout(
                        () => document.querySelector("[data-testid=meeting-actions]")?.scrollIntoView({ block: "center" }),
                        0,
                      );
                    }}
                    dangerouslySetInnerHTML={{ __html: leadFirst(summary.data, meeting.data.title) }}
                  />
                ) : (
                  <p data-testid="no-summary" className="mb-6 text-sm text-secondary">
                    {t("meeting.notRendered")}
                  </p>
                ))}

              {tab === "transcript" && (
                <Transcript
                  ref={findRef}
                  segments={segments}
                  people={people}
                  dir={contentDirection(meeting.data.language)}
                  speaking={spokenIndex}
                  onSeek={(seconds) => {
                    playerRef.current?.seek(seconds);
                    setPlayhead(seconds);
                  }}
                />
              )}
            </div>
            {tab === "summary" && summary.data && (
              <SummaryMinimap root={summaryRef.current} scroller={scroller} version={summary.data} />
            )}
          </div>
        </div>

        {/*
         * The rail. The reading column is capped on purpose; what was wrong was
         * leaving the rest of the pane empty.
         */}
        <MeetingRail meeting={meeting.data} segments={segments} tab={tab} onSeek={seekTo} />
      </div>

      {hasAudio && tab === "transcript" && (
        <AudioPlayer ref={playerRef} src={api.audioUrl(id, "mix")} onTime={setPlayhead} bands={bands} />
      )}
      {details && (
        <MeetingDetailsDialog meetingId={id} calendar={meeting.data.calendar} onClose={() => setDetails(false)} />
      )}
    </section>
  );
}
