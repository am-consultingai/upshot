import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Meeting } from "../api";
import { useI18n } from "../i18n";
import StateBadge from "../components/StateBadge";

const ATTENTION_STATES = ["FAILED", "DISCARDED", "INTERRUPTED"];

export default function Attention() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const queries = useQuery({
    queryKey: ["attention"],
    queryFn: async () => {
      const results = await Promise.all(
        ATTENTION_STATES.map((state) => api.meetings({ state })),
      );
      return results.flatMap((result) => result.meetings);
    },
  });
  const retry = useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: string }) => api.retry(id, stage),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const meetings: Meeting[] = queries.data ?? [];

  return (
    <section data-testid="attention-page">
      {meetings.length === 0 && <p data-testid="attention-empty">{t("attention.empty")}</p>}
      <ul className="grid gap-2">
        {meetings.map((meeting) => (
          <li
            key={meeting.id}
            data-testid="attention-item"
            data-meeting-id={meeting.id}
            className="flex items-center justify-between rounded border border-neutral-200 bg-white p-3"
          >
            <Link to={`/m/${meeting.id}`}>{meeting.title ?? meeting.id}</Link>
            <span className="flex items-center gap-2">
              <StateBadge state={meeting.state} />
              <button
                type="button"
                data-testid="retry"
                onClick={() => retry.mutate({ id: meeting.id, stage: "transcribe" })}
                className="rounded border border-neutral-300 px-2 py-1 text-sm"
              >
                {t("attention.retry")}
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
