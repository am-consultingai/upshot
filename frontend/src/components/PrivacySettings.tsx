import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import SettingRow, { SettingGroup } from "./SettingRow";

const SECONDARY = "rounded border border-line px-2.5 py-1 text-sm";

/**
 * Privacy (D87): the crash-report answer given in setup, changeable here, and the last
 * report exactly as it was sent, so nobody has to take our word for what is in one.
 */
export default function PrivacySettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const diagnostics = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics });
  const save = useMutation({
    mutationFn: (on: boolean) => api.putSettings({ "diagnostics.crash_reports": on ? "on" : "off" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["diagnostics"] }),
  });
  const [on, setOn] = useState<boolean | null>(null);
  const [showing, setShowing] = useState(false);
  const state = diagnostics.data;
  const report = state?.last_report ?? null;

  return (
    <SettingGroup>
      <SettingRow
        label={t("privacy.crashReports")}
        htmlFor="crash-reports"
        configKey="diagnostics.crash_reports"
        description={
          <>
            <span>{t("privacy.crashReportsHint")}</span>
            {state && !state.available && (
              <span data-testid="crash-reports-unavailable" className="mt-1 block text-tertiary">
                {t("privacy.unavailable")}
              </span>
            )}
          </>
        }
      >
        <input
          id="crash-reports"
          data-testid="crash-reports"
          type="checkbox"
          role="switch"
          checked={on ?? state?.consent === "on"}
          onChange={(event) => {
            setOn(event.target.checked);
            save.mutate(event.target.checked);
          }}
          className="size-4 accent-[var(--accent)]"
        />
      </SettingRow>

      <SettingRow
        label={t("privacy.lastReport")}
        description={!report ? <span data-testid="crash-report-none">{t("privacy.noReport")}</span> : undefined}
        tone={
          report &&
          showing && (
            <pre
              data-testid="crash-report-json"
              dir="ltr"
              className="mt-2 max-h-80 overflow-auto rounded bg-surface-2 p-3 font-mono text-2xs"
            >
              {JSON.stringify(report, null, 2)}
            </pre>
          )
        }
      >
        {report ? (
          <button
            type="button"
            data-testid="crash-report-show"
            className={SECONDARY}
            onClick={() => setShowing((value) => !value)}
          >
            {t(showing ? "privacy.hideReport" : "privacy.showReport")}
          </button>
        ) : (
          <span />
        )}
      </SettingRow>
    </SettingGroup>
  );
}
