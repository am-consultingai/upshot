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

/** What the detector decided. Only a failure is worth colour. */
const OUTCOME_TONE: Record<string, string> = {
  committed: "bg-success",
  failed: "bg-danger",
};

/** The app's own name for a process, which is all anyone wants to read. */
function appName(process: string | null | undefined): string {
  if (!process) return "—";
  return (process.split("\\").pop() ?? process).replace(/\.exe$/i, "");
}

export default function Detector() {
  const { t, locale } = useI18n();
  const events = useQuery({ queryKey: ["detector"], queryFn: api.detectorEvents });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const rows = events.data?.events ?? [];
  const mode = status.data?.detector.mode;

  return (
    <section data-testid="detector-page">
      <h1 className="display mb-3 text-2xl">{t("nav.detector")}</h1>

      {/* A table of scores with no word about what is being scored explained nothing. */}
      <p
        className="mb-4 max-w-prose text-sm leading-relaxed text-secondary"
        data-testid="detector-about"
      >
        {t("detector.about")}
      </p>

      {mode && MODE_NOTE[mode] && (
        <p
          className="mb-8 rounded-lg bg-surface-2 px-3 py-2 text-sm text-secondary"
          data-testid="detector-mode"
          data-mode={mode}
        >
          {t(MODE_NOTE[mode])}
        </p>
      )}

      <h2 className="mb-1 text-xs font-medium text-tertiary">{t("detector.events")}</h2>

      {events.isSuccess && rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-tertiary" data-testid="detector-empty">
          {t("detector.empty")}
        </p>
      ) : (
        /*
         * Rows rather than a table. The columns were mostly empty — a score is two
         * characters and an outcome is one word — so the grid spent its width on
         * alignment nobody needed, and the evidence, which is the only reason to
         * keep this log at all, was squeezed into the last column.
         */
        <div data-testid="detector-events" className="divide-y divide-line-subtle">
          {rows.map((event) => (
            <article
              key={event.id}
              data-testid="detector-event"
              data-outcome={event.outcome}
              className="-mx-2 flex items-baseline gap-3 rounded-md px-2 py-2.5 hover:bg-surface-1"
            >
              <span
                className={`size-1.5 shrink-0 translate-y-[-1px] rounded-full ${
                  OUTCOME_TONE[event.outcome] ?? "bg-border-strong"
                }`}
                aria-hidden="true"
              />

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium">{appName(event.process)}</span>
                  <span data-testid="detector-outcome" className="text-xs text-tertiary">
                    {event.outcome}
                  </span>
                </div>
                {/* Why it decided, in the quiet tier: detail, but the point of the log. */}
                <p className="mt-0.5 truncate text-xs text-tertiary">
                  {event.evidence.map((item) => item.code).join(" · ") || "—"}
                </p>
              </div>

              <span
                data-testid="detector-score"
                title={t("detector.score")}
                className="shrink-0 text-sm tabular-nums text-secondary"
              >
                {event.peak_score}
              </span>

              <span
                data-testid="detector-when"
                className="w-28 shrink-0 text-end text-xs tabular-nums text-tertiary"
              >
                {formatEventTime(event.at, locale)}
              </span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
