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

  const meeting = useQuery({ queryKey: ["meeting", id], queryFn: () => api.meeting(id) });
  const summary = useQuery({
    queryKey: ["summary", id],
    queryFn: () => api.summaryHtml(id),
    retry: false,
  });
  const transcript = useQuery({
    queryKey: ["transcript", id],
    queryFn: () => api.transcript(id),
    retry: false,
  });
  const rename = useMutation({
    mutationFn: (title: string) => api.patchMeeting(id, { title }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["meeting", id] }),
  });

  useEffect(() => {
    if (seeked !== null && audioRef.current) audioRef.current.currentTime = seeked;
  }, [seeked]);

  const dir = useMemo(
    () => contentDirection(meeting.data?.summary_language ?? meeting.data?.language ?? null),
    [meeting.data],
  );

  if (meeting.isLoading) return <p data-testid="loading">{t("common.loading")}</p>;
  if (!meeting.data) return <p data-testid="error">{t("common.error")}</p>;

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
          onClick={() => rename.mutate(`${meeting.data?.title ?? id} (renamed)`)}
        >
          {t("meeting.rename")}
        </button>
      </header>

      {meeting.data.evidence.length > 0 && (
        <p className="mb-4 text-sm text-neutral-600" data-testid="recorded-because">
          {t("meeting.recordedBecause")}:{" "}
          {meeting.data.evidence.map((item) => item.detail).join(", ")}
        </p>
      )}

      <h2 className="mb-2 text-sm font-semibold text-neutral-500">{t("meeting.summary")}</h2>
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

      <h2 className="mb-2 text-sm font-semibold text-neutral-500">{t("meeting.transcript")}</h2>
      <audio
        ref={audioRef}
        data-testid="audio"
        src={api.audioUrl(id, "them")}
        controls
        preload="none"
        className="mb-3 w-full"
      />
      <ol data-testid="transcript" dir={contentDirection(meeting.data.language)}>
        {(transcript.data?.segments ?? []).map((segment, index) => (
          <li key={index}>
            <button
              type="button"
              data-testid="transcript-turn"
              data-at-ms={Math.round(segment.start * 1000)}
              onClick={() => setSeeked(segment.start)}
              className="block w-full text-start"
            >
              <span className="text-xs text-neutral-500" data-testid="turn-speaker">
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
