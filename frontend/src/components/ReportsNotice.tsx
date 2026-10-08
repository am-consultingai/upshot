import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import Banner from "./Banner";
import Button from "./Button";

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
    <Banner
      data-testid="reports-notice"
      role="status"
      tone="notice"
      actions={
        <>
          <Button
            data-testid="reports-notice-yes"
            variant="ghost"
            disabled={answer.isPending}
            onClick={() => answer.mutate(true)}
          >
            {t("reportsNotice.yes")}
          </Button>
          <Button
            data-testid="reports-notice-no"
            variant="ghost"
            disabled={answer.isPending}
            onClick={() => answer.mutate(false)}
          >
            {t("reportsNotice.no")}
          </Button>
        </>
      }
    >
      <span>{t("reportsNotice.text")}</span>
    </Banner>
  );
}
