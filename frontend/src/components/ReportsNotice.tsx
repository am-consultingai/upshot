import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

/**
 * The crash-report question for an install that finished setup before setup asked it
 * (D87). The same two answers, once; until one is chosen nothing is sent. Shown only in
 * a build that can send reports.
 */
export default function ReportsNotice() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const diagnostics = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics });
  const answer = useMutation({
    mutationFn: (on: boolean) => api.putSettings({ "diagnostics.crash_reports": on ? "on" : "off" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["diagnostics"] }),
  });
  const state = diagnostics.data;
  if (!state?.available || state.consent !== "unset" || answer.isSuccess) return null;
  return (
    <div data-testid="reports-notice" role="status" className="border-b border-line bg-surface-2">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-2 text-sm">
        <span>{t("reportsNotice.text")}</span>
        <span className="ms-auto flex gap-2">
          <button
            type="button"
            data-testid="reports-notice-yes"
            className="rounded-md px-2 py-1 hover:bg-a-200"
            disabled={answer.isPending}
            onClick={() => answer.mutate(true)}
          >
            {t("reportsNotice.yes")}
          </button>
          <button
            type="button"
            data-testid="reports-notice-no"
            className="rounded-md px-2 py-1 hover:bg-a-200"
            disabled={answer.isPending}
            onClick={() => answer.mutate(false)}
          >
            {t("reportsNotice.no")}
          </button>
        </span>
      </div>
    </div>
  );
}
