import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { formatEventTime } from "../lib/format";
import type { MessageKey } from "../locales/en";

const MODE_NOTE: Record<string, MessageKey> = {
  off: "detector.modeOff",
  shadow: "detector.modeShadow",
  on: "detector.modeOn",
};

export default function Detector() {
  const { t, locale } = useI18n();
  const events = useQuery({ queryKey: ["detector"], queryFn: api.detectorEvents });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const rows = events.data?.events ?? [];
  const mode = status.data?.detector.mode;

  return (
    <section data-testid="detector-page">
      {/* A table of scores with no word about what is being scored explained nothing. */}
      <p className="mb-2 max-w-3xl text-sm text-secondary" data-testid="detector-about">
        {t("detector.about")}
      </p>
      {mode && MODE_NOTE[mode] && (
        <p className="mb-4 text-sm font-medium text-primary" data-testid="detector-mode" data-mode={mode}>
          {t(MODE_NOTE[mode])}
        </p>
      )}

      <h2 className="mb-2 text-sm font-semibold text-tertiary">{t("detector.events")}</h2>
      {events.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-secondary" data-testid="detector-empty">
          {t("detector.empty")}
        </p>
      ) : (
        <table className="w-full text-start text-sm">
          <thead className="text-xs text-tertiary">
            <tr>
              <th className="text-start font-medium">{t("detector.when")}</th>
              <th className="text-start font-medium">{t("detector.app")}</th>
              <th className="text-start font-medium">{t("detector.score")}</th>
              <th className="text-start font-medium">{t("detector.outcome")}</th>
              <th className="text-start font-medium">{t("detector.evidence")}</th>
            </tr>
          </thead>
          <tbody data-testid="detector-events">
            {rows.map((event) => (
              <tr key={event.id} data-testid="detector-event" data-outcome={event.outcome}>
                <td className="whitespace-nowrap" data-testid="detector-when">
                  {formatEventTime(event.at, locale)}
                </td>
                <td>{event.process ?? ""}</td>
                <td data-testid="detector-score">{event.peak_score}</td>
                <td data-testid="detector-outcome">{event.outcome}</td>
                <td>{event.evidence.map((item) => item.code).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
