import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import StateBadge from "../components/StateBadge";

function contentDirection(language: string | null): "rtl" | "ltr" {
  return language === "he" ? "rtl" : "ltr";
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
  const summarize = useMutation({
    // force: pressing this means redo it, not "redo it if you think it is stale".
    mutationFn: () => api.retry(id, "summarize", true),
    onSuccess: () => {
      setPipelineBusy(true);
      queryClient.invalidateQueries();
    },
  });

  // A stage that failed five times used to look exactly like one still running: the page
  // said "Queued" and never spoke again. Whatever the queue knows, the page now shows.
  const jobs = meeting.data?.jobs ?? [];
  const running = jobs.filter((job) => job.state === "pending" || job.state === "running");
  const failed = jobs.filter((job) => job.state === "failed");
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
        <button
          type="button"
          data-testid="rename"
          className="rounded border border-neutral-300 px-2 py-1 text-sm"
          onClick={() =>
            rename.mutate(`${meeting.data?.title ?? id} (renamed)`)
          }
        >
          {t("meeting.rename")}
        </button>
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
        <button
          type="button"
          data-testid="summarize"
          disabled={summarize.isPending}
          onClick={() => summarize.mutate()}
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs disabled:opacity-40"
        >
          {summary.data ? t("meeting.resummarize") : t("meeting.summarize")}
        </button>
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
        {running.length > 0 && (
          <span className="text-xs text-neutral-600" data-testid="stage-running">
            {running.map((job) => `${job.stage}: ${job.state}`).join(", ")}
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
          className="mb-6 rounded border border-neutral-200 bg-white p-3"
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
