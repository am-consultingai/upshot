import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

export default function Detector() {
  const { t } = useI18n();
  const events = useQuery({ queryKey: ["detector"], queryFn: api.detectorEvents });

  return (
    <section data-testid="detector-page">
      <h2 className="mb-2 text-sm font-semibold text-neutral-500">{t("detector.events")}</h2>
      <table className="w-full text-sm">
        <tbody data-testid="detector-events">
          {(events.data?.events ?? []).map((event) => (
            <tr key={event.id} data-testid="detector-event" data-outcome={event.outcome}>
              <td>{event.at}</td>
              <td>{event.process ?? ""}</td>
              <td data-testid="detector-score">{event.peak_score}</td>
              <td data-testid="detector-outcome">{event.outcome}</td>
              <td>{event.evidence.map((item) => item.code).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
