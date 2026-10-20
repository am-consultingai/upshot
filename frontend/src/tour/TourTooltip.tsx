import { useId } from "react";
import type { TooltipRenderProps } from "react-joyride";
import { useI18n } from "../i18n";
import Button from "../components/Button";
import type { MessageKey } from "../locales/en";
import { Logo } from "../components/Logo";

/** What each step carries for the tooltip, in `step.data`. */
export interface TourStepData {
  kind: "welcome" | "stop";
  title: MessageKey;
  body: MessageKey;
  /** Keycaps after the line, as the record button shows them. */
  keys?: string[];
  /** The stop's number and how many there are; the welcome card has none. */
  stop?: number;
  of?: number;
  /** Short names of the stops, for the welcome card. */
  stops?: MessageKey[];
}

/**
 * The tour's card, drawn on the app's own tokens. Joyride keeps the mechanics (where it
 * sits, the spotlight, the focus); this owns how it looks. One title and one line per
 * stop (the UI copy rule), the step count and the keycaps always left to right, so Hebrew
 * reads "1 / 4" and "Ctrl R".
 */
export default function TourTooltip({ step, isLastStep, backProps, primaryProps, skipProps, tooltipProps }: TooltipRenderProps) {
  const { t } = useI18n();
  const data = step.data as TourStepData;
  // The dialog is named by its title.
  const titleId = useId();

  if (data.kind === "welcome") {
    return (
      <div
        {...tooltipProps}
        aria-labelledby={titleId}
        data-testid="tour-welcome"
        className="w-[min(calc(100vw-32px),380px)] rounded-xl bg-raised p-5.5 text-start text-primary shadow-[var(--shadow-ring),var(--shadow-lg)]"
      >
        <span className="mb-3 grid size-9 place-items-center rounded-lg bg-accent text-on-accent">
          <Logo className="w-4.5" />
        </span>
        <h2 id={titleId} className="display mb-1.5 text-[1.375rem] leading-tight">{t(data.title)}</h2>
        <p className="mb-3.5 text-sm text-secondary">{t(data.body)}</p>
        <ol className="mb-4 flex flex-wrap gap-1.5" aria-label={t("tour.label")}>
          {(data.stops ?? []).map((key, index) => (
            <li key={key} className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-secondary">
              <bdi className="tabular-nums">{index + 1}</bdi> · {t(key)}
            </li>
          ))}
        </ol>
        <div className="flex items-center gap-2">
          <button
            {...skipProps}
            type="button"
            data-testid="tour-skip"
            className="me-auto rounded-sm px-0.5 py-1 text-xs text-tertiary hover:text-primary hover:underline"
          >
            {t("tour.notNow")}
          </button>
          <Button {...primaryProps} data-testid="tour-next" variant="primary" size="md">
            {t("tour.start")}
          </Button>
        </div>
      </div>
    );
  }

  const count = `${data.stop ?? 1} / ${data.of ?? 1}`;
  return (
    <div
      {...tooltipProps}
      aria-labelledby={titleId}
      data-testid="tour-tip"
      data-stop={data.stop}
      className="w-[min(calc(100vw-32px),320px)] rounded-[10px] bg-raised px-4 pt-3.5 pb-3 text-start text-primary shadow-[var(--shadow-ring),var(--shadow-lg)]"
    >
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-accent">{t("tour.label")}</span>
        <span data-testid="tour-count" dir="ltr" className="font-mono text-2xs text-tertiary [unicode-bidi:isolate]">
          {count}
        </span>
      </div>
      <h2 id={titleId} className="mb-1 text-[0.9375rem] font-semibold tracking-snug">{t(data.title)}</h2>
      <p className="mb-3 text-sm text-secondary">
        {t(data.body)}
        {data.keys && (
          <span dir="ltr" className="ms-1 inline-flex gap-0.75 align-[1px] [unicode-bidi:isolate]">
            {data.keys.map((key) => (
              <kbd key={key} className="rounded-2xs px-1 font-mono text-3xs text-tertiary shadow-[var(--shadow-ring-subtle)]">
                {key}
              </kbd>
            ))}
          </span>
        )}
      </p>
      <div className="flex items-center gap-1.5">
        <span className="me-auto flex gap-1" aria-hidden="true">
          {Array.from({ length: data.of ?? 0 }, (_, index) => (
            <span
              key={index}
              className={`block h-1.5 rounded-full ${index + 1 === data.stop ? "w-4 bg-accent" : "w-1.5 bg-line-strong"}`}
            />
          ))}
        </span>
        <Button {...skipProps} data-testid="tour-skip" variant="ghost">
          {t("tour.skip")}
        </Button>
        {(data.stop ?? 1) > 1 && (
          <Button {...backProps} data-testid="tour-back" variant="secondary">
            {t("tour.back")}
          </Button>
        )}
        <Button {...primaryProps} data-testid="tour-next" variant="primary">
          {isLastStep ? t("tour.done") : t("tour.next")}
        </Button>
      </div>
    </div>
  );
}
