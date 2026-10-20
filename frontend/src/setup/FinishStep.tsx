import { useI18n, type MessageKey } from "../i18n";
import Button from "../components/Button";
import { useSetupSnapshot } from "./backend";
import type { CaptureMode, StepId, Summarizer } from "./flow";
import { StepFrame, fill } from "./ui";

const SUMMARY: Record<string, MessageKey> = {
  "claude-subscription": "firstRun.done.sum.claude-subscription",
  "codex-subscription": "firstRun.done.sum.codex-subscription",
  "antigravity-subscription": "firstRun.done.sum.antigravity-subscription",
  gemini: "firstRun.done.sum.gemini",
  anthropic: "firstRun.done.sum.anthropic",
  openai: "firstRun.done.sum.openai",
  ollama: "firstRun.done.sum.ollama",
  none: "firstRun.done.sum.none",
};

/** How a summarizing provider is named to the user: "Claude", "Gemini, with your key". */
export function summaryName(provider: string, t: (key: MessageKey) => string): string {
  return SUMMARY[provider] ? t(SUMMARY[provider]) : t("firstRun.done.sum.other");
}

/** Whether `summaryName` has a name of its own for this provider. */
export function hasSummaryName(provider: string): boolean {
  return provider in SUMMARY;
}

/**
 * The last step: what setup set up, one line each, with Change beside it (setup plan,
 * §3.6). Change opens that step; walking on from it comes back here, since this is last.
 *
 * Crash reports are a tick here rather than a step of their own (D87), ticked unless the
 * user said no before (product owner, 2026-10-20, ClickUp decision Z8tj1hnz80), and shown
 * only in a build that can send them.
 *
 * After an install setup reopens here, not at the welcome (D69): "Welcome back", what is
 * kept, and Looks good to end it.
 */
export default function FinishStep({
  summarizer,
  capture,
  reports,
  onReports,
  reopened,
  finishing,
  onChange,
  onFinish,
  onBack,
}: {
  summarizer: Summarizer;
  capture: CaptureMode | null;
  reports: boolean;
  onReports: (on: boolean) => void;
  reopened: boolean;
  finishing: boolean;
  onChange: (step: StepId) => void;
  onFinish: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const snapshot = useSetupSnapshot();
  const { calendar, speakers } = snapshot;
  const name = (provider: string) => summaryName(provider, t);
  const summaries = summarizer.fallback
    ? fill(t("firstRun.done.sumThen"), { first: name(summarizer.provider), second: name(summarizer.fallback) })
    : name(summarizer.provider);
  const rows: { id: string; label: MessageKey; value: string; ok: boolean; step: StepId | null }[] = [
    {
      id: "calendar",
      label: "firstRun.done.calendar",
      value: calendar.accounts.length
        ? calendar.accounts.map((a) => a.address).join(", ")
        : t(calendar.available ? "firstRun.done.calendarSkipped" : "firstRun.done.calendarUnavailable"),
      ok: calendar.accounts.length > 0,
      step: calendar.available ? "calendar" : null,
    },
    { id: "summaries", label: "firstRun.done.summaries", value: summaries, ok: summarizer.provider !== "none", step: "services" },
    {
      id: "capture",
      label: "firstRun.done.capture",
      value: t(capture === "on" ? "firstRun.capture.auto" : capture === "shadow" ? "firstRun.capture.notify" : "firstRun.done.captureOff"),
      ok: capture !== null,
      step: "capture",
    },
    {
      id: "audio",
      label: "firstRun.done.audio",
      value: t(
        speakers.phase === "heard"
          ? "firstRun.done.audioOk"
          : speakers.phase === "silent"
            ? "firstRun.done.audioSilent"
            : "firstRun.done.audioUnchecked",
      ),
      ok: speakers.phase === "heard",
      step: "audio",
    },
  ];

  return (
    <StepFrame
      id="finish"
      title={reopened ? "firstRun.done.again" : "firstRun.done.title"}
      lead={<p>{t(reopened ? "firstRun.done.againLead" : "firstRun.done.lead")}</p>}
      footer={
        <>
          <Button data-testid="setup-finish" busy={finishing} variant="primary" size="md" onClick={onFinish}>
            {t(reopened ? "firstRun.done.looksGood" : "firstRun.done.start")}
          </Button>
          {!reopened && (
            <Button data-testid="setup-back" variant="ghost" size="md" onClick={onBack}>
              {t("firstRun.back")}
            </Button>
          )}
        </>
      }
    >
      <div className="rounded-xl bg-raised shadow-sm ring-1 ring-inset ring-line-subtle">
        <dl className="divide-y divide-line-subtle">
          {rows.map((row) => (
            <div key={row.id} data-testid={`done-${row.id}`} className="flex items-center gap-3 px-5 py-3.5">
              <span aria-hidden="true" className={`w-4 shrink-0 text-sm ${row.ok ? "text-success" : "text-tertiary"}`}>
                {row.ok ? "✓" : "–"}
              </span>
              <dt className="w-28 shrink-0 text-sm text-secondary">{t(row.label)}</dt>
              <dd className="min-w-0 flex-1 text-sm font-medium [overflow-wrap:anywhere]">{row.value}</dd>
              {row.step && (
                <button
                  type="button"
                  data-testid={`done-change-${row.id}`}
                  onClick={() => onChange(row.step!)}
                  className="shrink-0 text-sm text-accent underline underline-offset-2"
                  aria-label={`${t("firstRun.done.change")}: ${t(row.label)}`}
                >
                  {t("firstRun.done.change")}
                </button>
              )}
            </div>
          ))}
        </dl>
        {snapshot.reports.available && (
          <div className="border-t border-line-subtle px-5 py-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                data-testid="done-reports"
                checked={reports}
                onChange={(event) => onReports(event.target.checked)}
                className="mt-0.5 size-4 shrink-0 accent-[var(--accent)]"
              />
              <span>
                <span className="block text-sm font-medium">{t("firstRun.done.reports")}</span>
                <span className="block text-xs text-tertiary">{t("firstRun.done.reportsHint")}</span>
              </span>
            </label>
            <details className="mt-2 ps-7 text-xs text-secondary">
              <summary className="cursor-pointer text-accent">{t("firstRun.done.reportsWhat")}</summary>
              <p className="mt-2">
                <span className="font-medium">{t("firstRun.reports.sentTitle")}: </span>
                {t("firstRun.reports.sent")}
              </p>
              <p className="mt-1">
                <span className="font-medium">{t("firstRun.reports.neverTitle")}: </span>
                {t("firstRun.reports.never")}
              </p>
              <a
                href="https://upshot.amconsultingai.com/privacy.html"
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block underline"
              >
                {t("firstRun.reports.privacy")}
              </a>
            </details>
          </div>
        )}
      </div>
    </StepFrame>
  );
}
