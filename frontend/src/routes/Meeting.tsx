import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Job } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import { formatElapsed } from "../lib/format";
import StateBadge from "../components/StateBadge";
import BusyButton, { Spinner } from "../components/BusyButton";

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
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [seeked, setSeeked] = useState<number | null>(null);
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
  const rename = useMutation({
    mutationFn: (title: string) => api.patchMeeting(id, { title }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["meeting", id] }),
  });

  useEffect(() => {
    if (seeked !== null && audioRef.current)
      audioRef.current.currentTime = seeked;
  }, [seeked]);

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

  if (meeting.isLoading)
    return <p data-testid="loading">{t("common.loading")}</p>;
  if (!meeting.data) return <p data-testid="error">{t("common.error")}</p>;

  // Press play, hear the meeting. The server mixes both sides into one stream as it is
  // read; the per-track URLs still exist for diagnosis but the UI does not offer them —
  // nobody wants to choose a track before listening to their own meeting.
  const hasAudio = Object.keys(meeting.data.audio_tracks ?? {}).length > 0;

  return (
    <section data-testid="meeting-page" data-meeting-id={id}>
      <header className="mb-4 flex items-center gap-3">
        <h1 className="text-xl font-semibold" data-testid="meeting-title">
          {meeting.data.title ?? id}
        </h1>
        <StateBadge state={meeting.data.state} />
        <BusyButton
          data-testid="rename"
          busy={rename.isPending}
          className="rounded border border-neutral-300 px-2 py-1 text-sm"
          onClick={() =>
            rename.mutate(`${meeting.data?.title ?? id} (renamed)`)
          }
        >
          {t("meeting.rename")}
        </BusyButton>
      </header>

      {meeting.data.evidence.length > 0 && (
        <p
          className="mb-4 text-sm text-neutral-600"
          data-testid="recorded-because"
        >
          {t("meeting.recordedBecause")}:{" "}
          {meeting.data.evidence.map((item) => item.detail).join(", ")}
        </p>
      )}

      {hasAudio ? (
        <audio
          ref={audioRef}
          data-testid="audio"
          src={api.audioUrl(id, "mix")}
          controls
          preload="none"
          className="mb-3 w-full"
        />
      ) : meeting.data.audio_deleted_at ? (
        // Deleted on purpose, after the retention period. Saying "no audio" here would
        // read as a failed recording.
        <p
          data-testid="audio-deleted"
          className="mb-3 text-sm text-neutral-600"
        >
          {t("meeting.audioDeleted")}
        </p>
      ) : (
        <p data-testid="no-audio" className="mb-3 text-sm text-neutral-600">
          {t("meeting.noAudio")}
        </p>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-neutral-500">
          {t("meeting.summary")}
        </h2>
        <BusyButton
          data-testid="summarize"
          busy={working}
          onClick={() => summarize.mutate()}
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs"
        >
          {summary.data ? t("meeting.resummarize") : t("meeting.summarize")}
        </BusyButton>
        {/* The prompt decides everything about the summary, so the way to change the
            summary is one click away from it. */}
        <Link
          to="/settings#prompt"
          data-testid="view-prompt"
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs"
        >
          {t("meeting.viewPrompt")}
        </Link>
        {summary.data && (
          <button
            type="button"
            data-testid="copy-summary"
            onClick={() => copySummary(summary.data as string)}
            className="rounded border border-neutral-300 px-2 py-0.5 text-xs"
          >
            {copied ? t("meeting.copied") : t("meeting.copy")}
          </button>
        )}
        {working && (
          <span
            className="inline-flex items-center gap-1.5 text-xs text-neutral-700"
            data-testid="stage-running"
            data-stage={current?.stage ?? ""}
            data-state={current?.state ?? ""}
            role="status"
          >
            <Spinner className="text-blue-600" />
            {current?.state === "running"
              ? `${t(STAGE_LABEL[current.stage] ?? "meeting.stageWorking")}…`
              : `${t("meeting.stageWaiting")}: ${t(
                  STAGE_LABEL[current?.stage ?? "summarize"] ?? "meeting.stageWorking",
                )}`}
            {current?.state === "running" && current.started_at && (
              <span className="tabular-nums text-neutral-500" data-testid="stage-elapsed">
                {formatElapsed(current.started_at, now)}
              </span>
            )}
          </span>
        )}
      </div>

      {failed.length > 0 && (
        <div
          data-testid="stage-failed"
          className="mb-4 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800"
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
          className="summary-prose mb-6 rounded border border-neutral-200 bg-white p-4"
          dangerouslySetInnerHTML={{ __html: summary.data }}
        />
      ) : (
        <p data-testid="no-summary" className="mb-6 text-sm text-neutral-600">
          {t("meeting.notRendered")}
        </p>
      )}

      <h2 className="mb-2 text-sm font-semibold text-neutral-500">
        {t("meeting.transcript")}
      </h2>
      <ol
        data-testid="transcript"
        dir={contentDirection(meeting.data.language)}
      >
        {(transcript.data?.segments ?? []).map((segment, index) => (
          <li key={index}>
            <button
              type="button"
              data-testid="transcript-turn"
              data-at-ms={Math.round(segment.start * 1000)}
              onClick={() => setSeeked(segment.start)}
              className="block w-full text-start"
            >
              <span
                className="text-xs text-neutral-500"
                data-testid="turn-speaker"
              >
                {segment.speaker}
              </span>{" "}
              <span>{segment.text}</span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
