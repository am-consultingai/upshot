import type { ReactNode } from "react";
import { useI18n, type MessageKey } from "../i18n";

/** Fill `{name}` placeholders in a catalogue string. */
export function fill(text: string, values: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (whole, key: string) => (key in values ? String(values[key]) : whole));
}

export const PRIMARY =
  "rounded-md bg-accent px-3.5 py-1.5 text-sm font-medium text-on-accent hover:bg-accent-hover disabled:opacity-50";
export const SECONDARY = "rounded-md bg-surface-2 px-3 py-1.5 text-sm hover:bg-a-200 active:bg-a-300";
export const FOOTER =
  "sticky bottom-0 mt-auto flex flex-row-reverse items-center justify-between gap-2 bg-canvas pt-6 pb-8";
export const QUIET = "rounded-md px-3 py-1.5 text-sm text-secondary hover:bg-a-200 active:bg-a-300";

/**
 * One step of setup: a heading that says what it is for, a lead that says why, the
 * controls, and the way forward. Every step has the same frame, and the frame keeps
 * every part in the same box from step to step: the title on one line, then a lead
 * slot of fixed height whether the step has a lead or not, so the step's main content
 * (the cards, the panels, the choices) always starts on the same line and only what
 * is inside it changes. Keep a lead to two lines; the slot does not grow.
 */
export function StepFrame({
  id,
  title,
  icon,
  lead,
  children,
  footer,
}: {
  id: string;
  title: MessageKey;
  /** Beside the title, on its line (the Done tick). */
  icon?: ReactNode;
  lead?: ReactNode;
  children?: ReactNode;
  footer: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <section data-testid={`setup-step-${id}`} aria-labelledby={`setup-title-${id}`} className="flex w-full flex-1 flex-col">
      <h1
        id={`setup-title-${id}`}
        className="display mb-2 flex h-10 items-center justify-center gap-3 text-center text-3xl"
      >
        {icon}
        {t(title)}
      </h1>
      <div data-testid="setup-lead" className="mx-auto mb-6 h-12 max-w-2xl text-center text-base text-secondary">
        {lead}
      </div>
      <div data-testid="setup-content">{children}</div>
      {/*
       * Pinned to the bottom of the window, the same place on every step, and each
       * button in a slot of its own: the way forward (Continue, or Skip) always at the
       * end, Back always at the start. Footers list forward first, hence row-reverse.
       */}
      <div className={FOOTER}>{footer}</div>
    </section>
  );
}

export type Tone = "neutral" | "good" | "warn" | "bad";

const TONE: Record<Tone, string> = {
  neutral: "bg-surface-3 text-secondary",
  good: "bg-success-quiet text-success",
  warn: "bg-warning-quiet text-warning",
  bad: "bg-danger-quiet text-danger",
};

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}>
      {children}
    </span>
  );
}

/** A line of feedback under a control: what happened, in the colour of how it went. */
export function Note({ tone, children, testId }: { tone: Tone; children: ReactNode; testId?: string }) {
  const colour = { neutral: "text-secondary", good: "text-success", warn: "text-warning", bad: "text-danger" }[tone];
  return (
    <p data-testid={testId} role={tone === "bad" ? "alert" : undefined} className={`text-xs leading-relaxed ${colour}`}>
      {children}
    </p>
  );
}

/** The exact command something will run, shown before it runs. */
export function Command({ label, command }: { label: string; command: string }) {
  return (
    <div className="text-xs text-tertiary">
      <p className="mb-1">{label}</p>
      <code dir="ltr" className="block overflow-x-auto rounded bg-surface-2 px-2 py-1.5 font-mono text-secondary">
        {command}
      </code>
    </div>
  );
}
