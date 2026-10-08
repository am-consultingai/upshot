import { useEffect, useMemo, useRef, useState } from "react";
import { buttonClass } from "../components/Button";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason, type Job } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import { formatElapsed, formatShortDate } from "../lib/format";
import MeetingCalendarCard from "../components/MeetingCalendarCard";
import MeetingDetailsDialog from "../components/MeetingDetailsDialog";
import MeetingRail from "../components/MeetingRail";
import MeetingChips from "../components/MeetingChips";
import Menu from "../components/Menu";
import TranscribeAgainDialog from "../components/TranscribeAgainDialog";
import { openMeetingInfo } from "../components/MeetingInfoDialog";
import Tooltip from "../components/Tooltip";
import StateBadge from "../components/StateBadge";
import Button, { Spinner } from "../components/Button";
import AudioPlayer, { type AudioPlayerHandle, type Band } from "../components/AudioPlayer";
import ActionItemsBlock from "../components/ActionItemsBlock";
import SummaryMinimap from "../components/SummaryMinimap";
import SummaryRating from "../components/SummaryRating";
import Transcript from "../components/Transcript";
import { confirmDialog } from "../components/ConfirmDialog";
import { toast } from "../components/Toaster";
import { speakers } from "../lib/speakers";
import { markdownFilename, summaryToMarkdown } from "../lib/markdown";
import { leadFirst } from "../lib/summary";
import { meetingDirections } from "../lib/direction";
// What each stage is doing, in words. "summarize: running" told nobody anything.
import { STAGE_LABEL, formatPercent, phaseLabel, timeLeft, useMeetingJob } from "../lib/activeJobs";
import { Loading, Skeleton, SkeletonProse } from "../components/Skeleton";
import EmptyState, { EMPTY_BUTTON, EMPTY_ICON } from "../components/EmptyState";
import { shortcutKey, typing } from "../lib/keys";
import { onMeetingKey, type MeetingKey } from "../lib/meetingKeys";

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

/**
 * A control that Space already presses. Space on a focused button is that button's
 * own key, and taking it away would strand anyone working the page by keyboard; the
 * transcript's timestamps are the exception, since pressing one is "play from here"
 * and the next Space should pause what it started.
 */
function pressable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.closest("[data-testid=transcript-turn]")) return false;
  return (
    target.closest(
      "button,a[href],summary,[role=button],[role=checkbox],[role=switch],[role=tab],[role=option],[role=menuitem]",
    ) !== null
  );
}

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
  // An id merged into an earlier recording of the same meeting answers with that one
  // (D89): show it under its own address, so everything on the page asks about it.
  const resolvedId = meeting.data?.id;
  useEffect(() => {
    if (resolvedId && resolvedId !== id) navigate(`/m/${resolvedId}`, { replace: true });
  }, [resolvedId, id, navigate]);
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
  // "Assign to meeting…" from the library (D89): the calendar card, open at the choice.
  const assigning = params.get("assign") === "1";
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
  // The hidden "Transcribe again as…" (R9): only from the ⋯ menu, only once transcribed.
  const [transcribeAgain, setTranscribeAgain] = useState(false);
  const retranscribe = useMutation({
    mutationFn: (language: string) => api.retry(id, "transcribe", true, language),
    onSuccess: () => {
      setPressedAt(Date.now());
      setPipelineBusy(true);
      queryClient.invalidateQueries();
    },
  });
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
  // How far the running stage has got, where it can say (the transcription); a summary
  // has no honest percentage and keeps its spinner and elapsed time alone.
  const queued = useMeetingJob(id);
  const measured =
    current?.state === "running" && queued?.state === "running" && queued.stage === current.stage
      ? queued
      : undefined;
  const progress = measured?.progress ?? null;

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
  /*
   * Whether the transcript keeps the line being spoken in view. On until the reader
   * scrolls away to read something else, and back on when they ask for playback
   * again — a click on a line, play, J or K.
   */
  const [following, setFollowing] = useState(true);
  const [playing, setPlaying] = useState(false);
  // Leaving the pill removes the player, and a removed <audio> stops without a word.
  useEffect(() => {
    if (tab !== "transcript") setPlaying(false);
  }, [tab]);
  /*
   * The meeting page's keys, as a media player's are: Space plays and pauses, J and K
   * step to the next and previous line and play from there, and / finds in the
   * transcript (Ctrl+F too, which is what people try first). Kept in a ref so the one
   * listener always acts on the page as it is now.
   */
  const keyAction = useRef<(key: MeetingKey) => void>(() => undefined);
  keyAction.current = (key) => {
    if (key === "find") {
      setTab("transcript");
      window.setTimeout(() => findRef.current?.focus(), 0);
      return;
    }
    if (key === "play") {
      if (Object.keys(meeting.data?.audio_tracks ?? {}).length === 0) return;
      setFollowing(true);
      // The transport lives on the transcript pill: from the summary, Space opens it
      // and plays from where it was left.
      if (playerRef.current) playerRef.current.toggle();
      else seekTo(playhead);
      return;
    }
    const lines = transcript.data?.segments ?? [];
    if (lines.length === 0) return;
    let here = -1;
    for (let index = 0; index < lines.length && lines[index].start <= playhead + 0.05; index += 1) here = index;
    const next = key === "next" ? (playhead <= 0 ? 0 : here + 1) : Math.max(0, here - 1);
    if (next >= lines.length) return;
    setFollowing(true);
    seekTo(lines[next].start);
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && event.code === "KeyF") {
        event.preventDefault();
        keyAction.current("find");
        return;
      }
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return;
      if (document.querySelector("[role=dialog],[role=alertdialog],[role=menu]")) return;
      // The sidebar's list has its own J and K, for moving between meetings.
      if (event.target instanceof HTMLElement && event.target.closest("[role=listbox]")) return;
      const key = shortcutKey(event);
      const action: MeetingKey | null =
        key === " " ? "play" : key === "j" ? "next" : key === "k" ? "previous" : key === "/" ? "find" : null;
      if (!action || (action === "play" && pressable(event.target))) return;
      // Prevented here, in the capture phase, so the app-wide / (go to Search) sees
      // that it was taken and leaves it alone.
      event.preventDefault();
      keyAction.current(action);
    };
    document.addEventListener("keydown", onKey, true);
    const off = onMeetingKey((key) => keyAction.current(key));
    return () => {
      document.removeEventListener("keydown", onKey, true);
      off();
    };
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
    onError: (error) =>
      toast({
        title: t("toast.deleteFailed").replace("{title}", meeting.data?.title ?? id),
        sub: reason(error),
        tone: "danger",
      }),
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

  // Which way the notes and the transcript run, as the API says for their languages.
  const directions = useMemo(() => meetingDirections(meeting.data), [meeting.data]);
  const dir = directions.summary;

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

  if (meeting.isLoading) return <MeetingSkeleton />;
  if (!meeting.data)
    return (
      <EmptyState
        testid="error"
        tone="danger"
        icon={EMPTY_ICON.error}
        title={t("meeting.loadFailed")}
        body={t("meeting.loadFailedBody")}
        action={
          <>
            <button type="button" onClick={() => void meeting.refetch()} className={EMPTY_BUTTON}>
              {t("common.retry")}
            </button>
            <Link to="/" className={EMPTY_BUTTON}>
              {t("nav.timeline")}
            </Link>
          </>
        }
      />
    );

  // Press play, hear the meeting. The server mixes both sides into one stream as it is
  // read; the per-track URLs still exist for diagnosis but the UI does not offer them —
  // nobody wants to choose a track before listening to their own meeting.
  const hasAudio = Object.keys(meeting.data.audio_tracks ?? {}).length > 0;
  const people = speakers(segments, meeting.data.speaker_names ?? {}, meeting.data.calendar?.participants ?? [], {
    you: t("meeting.you"),
    them: t("meeting.themSaid"),
    speaker: t("meeting.speakerN"),
    mic: t("meeting.micN"),
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
            className={buttonClass("secondary")}
          >
            {t("calendar.joinMeeting")}
          </a>
        )}
        <StateBadge state={meeting.data.state} />
        {/*
         * One primary action per view. Beside the summary it is copying it — the
         * summary is written to be pasted into mail. Export sits beside it as a ghost
         * on both tabs: it is the other way the summary leaves the app, and it was
         * too far away at the bottom of the ⋯ menu.
         */}
        {summary.data && (
          <Tooltip label={t("meeting.export")} hint={t("help.export")}>
          <Button
            data-testid="export-bar"
            onClick={() => exportMarkdown(summary.data as string)}
            variant="secondary"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="size-3.5 fill-none stroke-current stroke-[1.5]"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M8 2.5v7.5M5 7l3 3 3-3M3 12.5v1h10v-1" />
            </svg>
            {t("meeting.export")}
          </Button>
          </Tooltip>
        )}
        {summary.data && tab === "summary" && (
          <Tooltip label={t("meeting.copy")} hint={t("help.copySummary")}>
          <Button
            data-testid="copy-summary-bar"
            onClick={() => copySummary(summary.data as string)}
            variant="primary"
          >
            {copied ? t("meeting.copied") : t("meeting.copy")}
          </Button>
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
            ...(meeting.data?.language
              ? [{ id: "transcribe-again", label: t("meeting.transcribeAgain"), run: () => setTranscribeAgain(true) }]
              : []),
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
            { id: "details", label: t("meeting.editDetails"), run: () => openMeetingInfo(id) },
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

              {meeting.data.description && (
                <p
                  data-testid="meeting-description"
                  dir="auto"
                  className="mb-4 whitespace-pre-line text-sm text-secondary"
                  onDoubleClick={() => openMeetingInfo(id)}
                >
                  {meeting.data.description}
                </p>
              )}

              {/*
               * A proposed match is a question only the user can answer, so it stays on
               * the page until they do. A settled one lives behind the people chip.
               */}
              {(matchState === "proposed" ||
                meeting.data.needs_meeting ||
                meeting.data.merge_with ||
                assigning) && (
                <MeetingCalendarCard
                  meetingId={id}
                  calendar={meeting.data.calendar}
                  needsMeeting={Boolean(meeting.data.needs_meeting)}
                  mergeWith={meeting.data.merge_with ?? null}
                  startPicking={assigning}
                />
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
                    aria-selected={tab === value}
                    onClick={() => setTab(value)}
                    className={`h-control-sm rounded-sm px-3.5 text-sm ${
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
                  <Button
                    data-testid="keep-meeting"
                    busy={keep.isPending}
                    onClick={() => keep.mutate()}
                    variant="primary"
                    className="ms-auto"
                  >
                    {t("meeting.keepAnyway")}
                  </Button>
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
                  {progress !== null && (
                    <span className="tabular-nums" data-testid="stage-progress">
                      {[phaseLabel(measured?.phase, t), formatPercent(progress, locale)]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  )}
                  {current?.state === "running" && current.started_at && (
                    <span className="tabular-nums text-tertiary" data-testid="stage-elapsed">
                      {formatElapsed(current.started_at, now)}
                    </span>
                  )}
                  {progress !== null && timeLeft(measured?.eta_s, t) && (
                    <span className="text-tertiary" data-testid="stage-eta">
                      {timeLeft(measured?.eta_s, t)}
                    </span>
                  )}
                </p>
              )}
              {working && progress !== null && (
                <div
                  className="-mt-1.5 mb-3 h-1 w-48 max-w-full overflow-hidden rounded-full bg-surface-2"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(progress * 100)}
                  data-testid="stage-progress-bar"
                >
                  <div className="h-full bg-accent" style={{ width: `${Math.round(progress * 100)}%` }} />
                </div>
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
              {/* First in, first out with file transcriptions (D86, R5): say what it waits behind. */}
              {current?.state === "pending" && (meeting.data?.files_ahead ?? 0) > 0 && (
                <p data-testid="files-ahead" className="mb-3 max-w-prose text-xs text-secondary">
                  {t("meeting.filesAhead").replace("{n}", String(meeting.data?.files_ahead ?? 0))}
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
                  <Button
                    data-testid="retry-summarize"
                    onClick={() => summarize.mutate()}
                    variant="secondary"
                    className="mt-2.5"
                  >
                    {t("meeting.summarize")}
                  </Button>
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
                ) : summary.isLoading ? (
                  <Loading testid="summary-loading" className="mb-12">
                    <SkeletonProse lines={4} className="mb-6" />
                    <Skeleton className="mb-3 h-3.5 w-40" />
                    <SkeletonProse lines={5} />
                  </Loading>
                ) : (
                  <NoSummary
                    working={working}
                    failed={failed.length > 0}
                    transcribed={segments.length > 0}
                    onSummarize={() => summarize.mutate()}
                    onTranscript={() => setTab("transcript")}
                  />
                ))}
              {/* Was it good? Up or down, sent only when the user sends it (D87, D3). */}
              {tab === "summary" && summary.data && <SummaryRating meetingId={meeting.data.id} />}

              {tab === "transcript" && transcript.isLoading && (
                <Loading testid="transcript-loading">
                  {[0, 1, 2].map((turn) => (
                    <div key={turn} className="pt-5 first:pt-0">
                      <div className="mb-2 flex items-center gap-2">
                        <Skeleton className="size-4 rounded-full" />
                        <Skeleton className="h-3 w-24" />
                      </div>
                      <SkeletonProse lines={turn === 1 ? 2 : 3} />
                    </div>
                  ))}
                </Loading>
              )}
              {tab === "transcript" && !transcript.isLoading && segments.length === 0 && (
                <EmptyState
                  testid="no-transcript"
                  icon={EMPTY_ICON.transcript}
                  title={t("meeting.noTranscriptTitle")}
                  body={working ? t("meeting.noTranscriptWorking") : t("meeting.noTranscriptBody")}
                  className="rounded-lg bg-surface-1"
                />
              )}
              {tab === "transcript" && segments.length > 0 && (
                <Transcript
                  ref={findRef}
                  segments={segments}
                  people={people}
                  dir={directions.transcript}
                  speaking={spokenIndex}
                  following={following && playing}
                  onUnfollow={() => setFollowing(false)}
                  onSeek={(seconds) => {
                    setFollowing(true);
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
        <AudioPlayer
          ref={playerRef}
          src={api.audioUrl(id, "mix")}
          onTime={setPlayhead}
          onPlaying={(now) => {
            setPlaying(now);
            if (now) setFollowing(true);
          }}
          bands={bands}
        />
      )}
      {transcribeAgain && (
        <TranscribeAgainDialog
          candidates={meeting.data.language_candidates}
          onClose={() => setTranscribeAgain(false)}
          onChoose={(code) => {
            setTranscribeAgain(false);
            retranscribe.mutate(code);
          }}
        />
      )}
      {details && (
        <MeetingDetailsDialog meetingId={id} calendar={meeting.data.calendar} onClose={() => setDetails(false)} />
      )}
    </section>
  );
}

/**
 * The page before the meeting has arrived: the bar, the title, the chips, the two
 * pills and a summary's worth of lines, where each of them will be.
 */
function MeetingSkeleton() {
  return (
    <Loading className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 flex-none items-center gap-2 border-b border-line-subtle px-5">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-3 w-24" />
        <Skeleton className="ms-auto h-7 w-24 rounded-md" />
      </div>
      <div className="px-8 pt-7">
        <div className="w-full max-w-[37rem]">
          <Skeleton className="mb-4 h-8 w-2/3 rounded-md" />
          <div className="mb-5 flex gap-1.5">
            <Skeleton className="h-6 w-24 rounded-full" />
            <Skeleton className="h-6 w-14 rounded-full" />
            <Skeleton className="h-6 w-20 rounded-full" />
          </div>
          <Skeleton className="mb-7 h-7 w-44 rounded-md" />
          <SkeletonProse lines={4} className="mb-6" />
          <SkeletonProse lines={5} />
        </div>
      </div>
    </Loading>
  );
}

/**
 * The summary's place when there is no summary, which is one of three situations
 * with three different next steps: it is being written (wait for it), the work failed
 * (the box above says why and offers Summarize), or nothing has produced one yet
 * (summarize, or read the transcript meanwhile).
 */
function NoSummary({
  working,
  failed,
  transcribed,
  onSummarize,
  onTranscript,
}: {
  working: boolean;
  failed: boolean;
  transcribed: boolean;
  onSummarize: () => void;
  onTranscript: () => void;
}) {
  const { t } = useI18n();
  if (working) {
    // Shaped like the summary it is waiting for; the stage line above says which step.
    return (
      <div data-testid="no-summary" data-reason="working" className="mb-12">
        <p className="mb-4 text-sm text-secondary">{t("meeting.noSummaryWorking")}</p>
        <div aria-hidden="true">
          <SkeletonProse lines={4} className="mb-6" />
          <SkeletonProse lines={3} />
        </div>
      </div>
    );
  }
  return (
    <EmptyState
      testid="no-summary"
      icon={EMPTY_ICON.page}
      title={t("meeting.notRendered")}
      body={
        failed
          ? t("meeting.noSummaryFailed")
          : transcribed
            ? t("meeting.noSummaryBody")
            : t("meeting.noSummaryNoTranscript")
      }
      className="mb-8 rounded-lg bg-surface-1"
      action={
        // A failure already offers Summarize in its own box: one button per question.
        failed || !transcribed ? undefined : (
          <>
            <button type="button" data-testid="no-summary-summarize" onClick={onSummarize} className={EMPTY_BUTTON}>
              {t("meeting.summarize")}
            </button>
            <button type="button" onClick={onTranscript} className={EMPTY_BUTTON}>
              {t("meeting.readTranscript")}
            </button>
          </>
        )
      }
    />
  );
}
