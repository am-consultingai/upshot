import type { HTMLAttributes, ReactNode } from "react";

/**
 * How a badge reads. Colour is for the states a glance must catch; everything else is
 * neutral, and `quiet` drops the fill altogether so a normal state does not shout as
 * loudly as a failure (see StateBadge).
 */
export type BadgeTone = "neutral" | "good" | "warn" | "bad";

const FILL: Record<BadgeTone, string> = {
  neutral: "bg-surface-3 text-secondary",
  good: "bg-success-quiet text-success",
  warn: "bg-warning-quiet text-warning",
  bad: "bg-danger-quiet text-danger",
};

const DOT: Record<BadgeTone, string> = {
  neutral: "bg-line-strong",
  good: "bg-success",
  warn: "bg-warning",
  bad: "bg-danger",
};

/**
 * A short label in a pill: a state, a plan, a "Ready". One shape everywhere; the tone is
 * the meaning. `dot` puts a dot before the label, so the colour is never the only signal.
 */
export default function Badge({
  tone = "neutral",
  quiet = false,
  dot = false,
  className = "",
  children,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  /** Quiet text, no fill: for the settled, unremarkable states. The dot keeps the tone. */
  quiet?: boolean;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      {...rest}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full text-xs ${
        quiet ? "text-tertiary" : `${FILL[tone]} px-2 py-0.5 font-medium`
      } ${className}`}
    >
      {dot && <span className={`size-1.5 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden="true" />}
      {children}
    </span>
  );
}

/**
 * The same badge as a small square holding a glyph instead of a word, for places too
 * tight for a label: a calendar chip's "recorded", "scheduled" and "failed" flags.
 * `live` is the recording one, a solid red square with the pulse in it.
 */
export function IconBadge({
  tone = "neutral",
  live = false,
  children,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; live?: boolean; children?: ReactNode }) {
  return (
    <span
      aria-hidden="true"
      {...rest}
      className={`grid size-3.75 shrink-0 place-items-center rounded-xs ${
        live
          ? "bg-danger"
          : `bg-raised shadow-[var(--shadow-ring-subtle)] ${tone === "bad" ? "text-danger" : "text-secondary"}`
      }`}
    >
      {live ? <span className="ma-pulse size-1.5 rounded-full bg-on-solid" /> : children}
    </span>
  );
}
