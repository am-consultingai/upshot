import { useQuery } from "@tanstack/react-query";
import { api, type Transcription } from "../api";
import { useI18n } from "../i18n";
import { textDirection } from "../lib/direction";
import { formatOffset } from "../lib/format";

/** A finished transcript, read-only: who spoke when. It runs in its language's direction. */
export default function TranscriptView({ job }: { job: Transcription }) {
  const { t } = useI18n();
  const result = useQuery({
    queryKey: ["transcription-result", job.id],
    queryFn: () => api.transcriptionResult(job.id),
  });
  if (result.isLoading) return <p className="px-4 py-3 text-sm text-tertiary">…</p>;
  const segments = result.data?.segments ?? [];
  if (!segments.length) {
    return (
      <p data-testid="transcript-view-empty" className="px-4 py-3 text-sm text-tertiary">
        {t("transcriptions.noSpeech")}
      </p>
    );
  }
  return (
    <div
      data-testid="transcript-view"
      dir={textDirection(job.direction)}
      className="max-h-96 space-y-2 overflow-y-auto px-4 py-3 text-sm leading-relaxed"
    >
      {segments.map((segment) => (
        <p key={segment.id}>
          <span className="me-2 font-mono text-2xs text-tertiary" dir="ltr">
            {formatOffset(segment.start)}
          </span>
          <span className="me-1.5 font-medium">{segment.speaker}</span>
          {segment.text}
        </p>
      ))}
    </div>
  );
}
