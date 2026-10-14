import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason, type AiActivityEntry } from "../api";
import { useI18n } from "../i18n";
import { formatShortDate } from "../lib/format";
import { DEFAULT_FORMATS, formatTime, type LocaleFormats } from "../lib/timeFormat";
import { buttonClass } from "./Button";
import { confirmDialog } from "./ConfirmDialog";
import EmptyState, { EMPTY_BUTTON, EMPTY_ICON } from "./EmptyState";
import { Loading, Skeleton } from "./Skeleton";
import { toast } from "./Toaster";

/** Asked again this often while the tab is open: calls are not pushed as events, since
 * every event refetches the whole page and an AI app can make dozens a minute. */
const REFRESH_MS = 5000;

const OUTCOME_TONE: Record<AiActivityEntry["outcome"], string> = {
  ok: "text-secondary",
  refused: "text-warning",
  error: "text-danger",
};

function Row({ entry, formats }: { entry: AiActivityEntry; formats: LocaleFormats }) {
  const { t, locale } = useI18n();
  const when = new Date(entry.at);
  return (
    <li
      data-testid="ai-activity-row"
      className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg bg-raised px-4 py-2.5 text-sm shadow-sm"
    >
      <span className="w-36 shrink-0 font-mono text-2xs text-tertiary tabular-nums">
        {formatShortDate(when, locale, false)} {formatTime(when, formats)}
      </span>
      <span className="w-32 shrink-0 truncate font-medium" dir="auto" title={entry.agent}>
        {entry.agent || t("aiActivity.unnamed")}
      </span>
      <span className="w-36 shrink-0 font-mono text-xs" dir="ltr">
        {entry.action}
      </span>
      <span className="min-w-0 flex-1 truncate" dir="auto" title={entry.target}>
        {entry.target}
      </span>
      <span data-testid="ai-activity-outcome" className={`shrink-0 text-xs ${OUTCOME_TONE[entry.outcome]}`}>
        {t(`aiActivity.outcome.${entry.outcome}`)}
      </span>
    </li>
  );
}

/**
 * What the user's own AI tools and programs asked of Upshot (D98): every meeting tool
 * call and every request to the transcription API, newest first, by app.
 */
export default function AiActivity() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [agent, setAgent] = useState("");
  const activity = useQuery({
    queryKey: ["ai-activity", agent],
    queryFn: () => api.aiActivity(agent),
    refetchInterval: REFRESH_MS,
  });
  const clear = useMutation({
    mutationFn: api.clearAiActivity,
    onSuccess: () => {
      setAgent("");
      void queryClient.invalidateQueries({ queryKey: ["ai-activity"] });
    },
    onError: (error) => toast({ title: reason(error), tone: "danger" }),
  });
  const formats = useQuery({ queryKey: ["locale"], queryFn: api.locale, staleTime: Infinity });
  const entries = activity.data?.entries ?? [];
  const agents = activity.data?.agents ?? [];

  return (
    <div data-testid="ai-activity">
      {(entries.length > 0 || agent) && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <span className="text-secondary">{t("aiActivity.filter")}</span>
            <select
              data-testid="ai-activity-filter"
              value={agent}
              onChange={(event) => setAgent(event.target.value)}
              className="h-control-sm rounded-md bg-surface-1 px-2 text-sm shadow-[var(--shadow-ring-subtle)]"
            >
              <option value="">{t("aiActivity.allApps")}</option>
              {agents.map((name) => (
                <option key={name} value={name}>
                  {name || t("aiActivity.unnamed")}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            data-testid="ai-activity-clear"
            disabled={clear.isPending}
            onClick={() =>
              void confirmDialog({
                title: t("aiActivity.clearTitle"),
                body: t("aiActivity.clearBody"),
                confirm: t("aiActivity.clear"),
                cancel: t("common.cancel"),
              }).then((yes) => yes && clear.mutate())
            }
            className={`${buttonClass("ghost", "md")} ms-auto`}
          >
            {t("aiActivity.clear")}
          </button>
        </div>
      )}
      <ul className="space-y-1" data-testid="ai-activity-list">
        {entries.map((entry) => (
          <Row key={entry.id} entry={entry} formats={formats.data ?? DEFAULT_FORMATS} />
        ))}
      </ul>
      {activity.isLoading && (
        <Loading className="space-y-1">
          {[0, 1, 2].map((index) => (
            <div key={index} className="flex items-center gap-4 rounded-lg bg-raised px-4 py-3 shadow-sm">
              <Skeleton className="h-3 w-24" />
              <Skeleton className={`h-3 ${index === 1 ? "w-1/3" : "w-1/2"}`} />
            </div>
          ))}
        </Loading>
      )}
      {activity.isError && !activity.data && (
        <EmptyState
          testid="ai-activity-error"
          tone="danger"
          icon={EMPTY_ICON.error}
          title={t("common.error")}
          body={t("aiActivity.errorBody")}
          action={
            <button type="button" onClick={() => void activity.refetch()} className={EMPTY_BUTTON}>
              {t("common.retry")}
            </button>
          }
        />
      )}
      {activity.data && !entries.length && !agent && (
        <EmptyState
          testid="ai-activity-empty"
          icon={EMPTY_ICON.search}
          title={t("aiActivity.emptyTitle")}
          body={t("aiActivity.empty")}
          action={
            <Link to="/settings#transcription" className={EMPTY_BUTTON}>
              {t("aiActivity.emptyLink")}
            </Link>
          }
        />
      )}
    </div>
  );
}
