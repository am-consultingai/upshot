import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { groupByDay } from "../lib/timeline";
import MeetingCard from "../components/MeetingCard";

export default function Timeline() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings() });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });

  const start = useMutation({
    mutationFn: api.startRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  if (meetings.isLoading) return <p data-testid="loading">{t("common.loading")}</p>;
  if (meetings.isError) return <p data-testid="error">{t("common.error")}</p>;

  const days = groupByDay(meetings.data?.meetings ?? []);

  return (
    <section data-testid="timeline">
      <div className="mb-4 flex items-center gap-3">
        <button
          type="button"
          data-testid="start-recording"
          disabled={status.data?.recorder.active}
          onClick={() => start.mutate()}
          className="rounded bg-neutral-900 px-3 py-1.5 text-white disabled:opacity-40"
        >
          {t("timeline.start")}
        </button>
        <span className="text-sm text-neutral-600" data-testid="queue-depth">
          {t("timeline.queued")}: {status.data?.queue_depth ?? 0}
        </span>
      </div>
      {days.length === 0 && <p data-testid="timeline-empty">{t("timeline.empty")}</p>}
      {days.map(([day, items]) => (
        <div key={day} data-testid="timeline-day" data-day={day} className="mb-6">
          <h2 className="mb-2 text-sm font-semibold text-neutral-500">{day}</h2>
          <div className="grid gap-2">
            {items.map((meeting) => (
              <MeetingCard
                key={meeting.id}
                meeting={meeting}
                onStop={() => stop.mutate()}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
