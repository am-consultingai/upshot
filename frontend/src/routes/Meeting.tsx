import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Job } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import { formatElapsed } from "../lib/format";
import MeetingCalendarCard from "../components/MeetingCalendarCard";
import StateBadge from "../components/StateBadge";
import BusyButton, { Spinner } from "../components/BusyButton";
import AudioPlayer, { type AudioPlayerHandle } from "../components/AudioPlayer";

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

/** The stage actually working, else the first one waiting. */
function currentJob(jobs: Job[]): Job | undefined {
  return jobs.find((job) => job.state === "running") ?? jobs.find((job) => job.state === "pending");
}

export default function MeetingPage() {
  const { id = "" } = useParams();
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const playerRef = useRef<AudioPlayerHandle | null>(null);
  // Where playback is now, so the transcript can show which line is being spoken.
  const [playhead, setPlayhead] = useState(0);
  // Polled while the pipeline is working, so a queued stage visibly finishes — or
  // visibly fails — instead of leaving the page on a hopeful message forever.
  const [pipelineBusy, setPipelineBusy] = useState(false);

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

  return (
    <section data-testid="meeting-page" data-meeting-id={id} className="flex h-full min-h-0 flex-col">
      {/*
        * A reading measure, not the width of the window. Every application in this
        * category caps it and they land within sixty pixels of each other —
        * Granola 640, Reflect 672, Obsidian 700 — because a summary is prose and
        * prose stops being readable somewhere past 80 characters a line. The pane
        * can be 900px wide; the text should not be.
        */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[42rem] px-7 py-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        {draft === null ? (
          <h1 className="display me-auto text-2xl" data-testid="meeting-title">
            {rename.isPending ? rename.variables : (meeting.data.title ?? id)}
          </h1>
        ) : (
          /*
           * The heading becomes the field, at the heading's size, so renaming reads as
           * editing the name in place rather than filling in a form. Enter or leaving
           * the field saves; Escape puts the old name back.
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
            className="display me-auto min-w-0 flex-1 rounded-md bg-surface-2 px-2 py-0.5 text-2xl"
          />
        )}
        <StateBadge state={meeting.data.state} />
        {draft === null && (
          <BusyButton
            data-testid="rename"
            busy={rename.isPending}
            className="rounded-md px-2 py-1 text-xs text-tertiary hover:bg-surface-2 hover:text-primary"
            onClick={() => setDraft(meeting.data?.title ?? "")}
          >
            {t("meeting.rename")}
          </BusyButton>
        )}
      </header>

      <MeetingCalendarCard meetingId={id} calendar={meeting.data.calendar} />

      {meeting.data.evidence.length > 0 && (
        <p
          className="mb-4 text-sm text-secondary"
          data-testid="recorded-because"
        >
          {t("meeting.recordedBecause")}:{" "}
          {meeting.data.evidence.map((item) => item.detail).join(", ")}
        </p>
      )}

      {meeting.data.state === "DISCARDED" && (
        /*
         * A recording shorter than the minimum is filed rather than transcribed,
         * which is the right default: the detector wakes on a notification chime
         * often enough that without it the library fills with eight-second
         * meetings. Nothing was deleted — the audio is on disk and the state
         * machine has always allowed the way back — but until now nothing offered
         * it, and a conversation you cannot reach is lost whatever the database
         * says.
         */
        <div
          data-testid="discarded-notice"
          className="mb-5 flex flex-wrap items-center gap-3 rounded-lg bg-surface-2 px-3 py-2.5 text-sm"
        >
          <span className="text-secondary">{t("meeting.discardedShort")}</span>
          <BusyButton
            data-testid="keep-meeting"
            busy={keep.isPending}
            onClick={() => keep.mutate()}
            className="ms-auto rounded-sm bg-accent px-2.5 py-1 text-xs font-medium text-on-accent"
          >
            {t("meeting.keepAnyway")}
          </BusyButton>
        </div>
      )}

      {meeting.data.audio_deleted_at ? (
        // Deleted on purpose, after the retention period. Saying "no audio" here would
        // read as a failed recording.
        <p data-testid="audio-deleted" className="mb-4 text-sm text-secondary">
          {t("meeting.audioDeleted")}
        </p>
      ) : !hasAudio ? (
        <p data-testid="no-audio" className="mb-4 text-sm text-secondary">
          {t("meeting.noAudio")}
        </p>
      ) : null}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-medium text-tertiary">{t("meeting.summary")}</h2>
        <BusyButton
          data-testid="summarize"
          busy={working}
          onClick={() => summarize.mutate()}
          className="rounded-sm bg-surface-2 px-2 py-1 text-xs text-secondary hover:text-primary"
        >
          {summary.data ? t("meeting.resummarize") : t("meeting.summarize")}
        </BusyButton>
        {/* The prompt decides everything about the summary, so the way to change the
            summary is one click away from it. */}
        <Link
          to="/settings#prompt"
          data-testid="view-prompt"
          className="rounded-sm bg-surface-2 px-2 py-1 text-xs text-secondary hover:text-primary"
        >
          {t("meeting.viewPrompt")}
        </Link>
        {summary.data && (
          <button
            type="button"
            data-testid="copy-summary"
            onClick={() => copySummary(summary.data as string)}
            className="rounded-sm bg-surface-2 px-2 py-1 text-xs text-secondary hover:text-primary"
          >
            {copied ? t("meeting.copied") : t("meeting.copy")}
          </button>
        )}
        {working && (
          <span
            className="inline-flex items-center gap-1.5 text-xs text-secondary"
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
          </span>
        )}
      </div>

      {failed.length > 0 && (
        <div
          data-testid="stage-failed"
          className="mb-4 rounded-lg bg-danger-quiet p-3 text-sm text-danger"
        >
          {failed.map((job) => (
            <p key={job.stage}>
              <strong>{job.stage}</strong> {t("meeting.stageFailed")} ({job.attempts})
              {job.last_error ? `: ${job.last_error}` : ""}
            </p>
          ))}
        </div>
      )}
      {summary.data ? (
        <div
          data-testid="summary-html"
          dir={dir}
          className="summary-prose mb-8 rounded-xl bg-raised p-6 shadow-md"
          dangerouslySetInnerHTML={{ __html: summary.data }}
        />
      ) : (
        <p data-testid="no-summary" className="mb-6 text-sm text-secondary">
          {t("meeting.notRendered")}
        </p>
      )}

      <h2 className="mb-3 text-xs font-medium text-tertiary">{t("meeting.transcript")}</h2>

      {/*
       * Two sides, by recorded track rather than by speaker name.
       *
       * This app captures two streams — the microphone and everything the machine
       * played — and that is all it actually knows. It has no diarisation, so the
       * "speaker" on a segment is the track it came from. Granola shows the same
       * data the same way, and for the same reason: system audio on one side, your
       * own microphone on the other. Inventing names the app cannot know would be
       * worse than showing the two sides it can.
       *
       * The sides are flipped with logical properties, so a Hebrew transcript
       * mirrors correctly without a second layout.
       */}
      <ol data-testid="transcript" dir={contentDirection(meeting.data.language)} className="space-y-1.5 pb-2">
        {(transcript.data?.segments ?? []).map((segment, index) => {
          const speaking = index === spokenIndex;
          const mine = segment.speaker === "ME";
          return (
            <li key={index} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <button
                type="button"
                data-testid="transcript-turn"
                data-at-ms={Math.round(segment.start * 1000)}
                data-track={mine ? "me" : "them"}
                data-speaking={speaking ? "true" : undefined}
                onClick={() => playerRef.current?.seek(segment.start)}
                className={`max-w-[38rem] rounded-lg px-3 py-2 text-start leading-relaxed transition-colors ${
                  mine ? "bg-accent-quiet" : "bg-surface-2"
                } ${speaking ? "ring-1 ring-accent" : "hover:brightness-[.98]"}`}
              >
                <span
                  data-testid="turn-speaker"
                  className="mb-0.5 block text-2xs font-medium text-tertiary"
                >
                  {mine ? t("meeting.meSaid") : t("meeting.themSaid")}
                </span>
                <span className="text-md">{segment.text}</span>
              </button>
            </li>
          );
        })}
      </ol>
        </div>
      </div>

      {hasAudio && (
        <AudioPlayer ref={playerRef} src={api.audioUrl(id, "mix")} onTime={setPlayhead} />
      )}
    </section>
  );
}
