import type { ReactNode } from "react";

/**
 * What a screen says when it has nothing to show, or could not show it.
 *
 * A grey sentence used to do this everywhere, and a grey sentence answers "is it
 * broken?" and nothing else. Every empty state now carries the same three parts: a
 * mark, a line that names the situation, and a line that says what happens next —
 * with the one control that makes it happen, where there is one. The mark is drawn
 * on the same 16px grid as the navigation icons, at stroke 1.5.
 */
export default function EmptyState({
  testid,
  icon,
  title,
  body,
  action,
  tone = "neutral",
  compact = false,
  className = "",
}: {
  testid?: string;
  /** An SVG path on a 16px grid. */
  icon: string;
  title: ReactNode;
  body?: ReactNode;
  /** The next step: a button or a link. */
  action?: ReactNode;
  tone?: "neutral" | "danger";
  /** For a narrow column (the sidebar) or a slot inside a page. */
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      data-testid={testid}
      data-tone={tone}
      className={`flex flex-col items-center text-center ${compact ? "px-3 py-8" : "px-6 py-12"} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`mb-3 grid place-items-center rounded-lg shadow-[var(--shadow-ring-subtle)] ${
          compact ? "size-8" : "size-10"
        } ${tone === "danger" ? "bg-danger-quiet text-danger" : "bg-surface-2 text-secondary"}`}
      >
        <svg
          viewBox="0 0 16 16"
          className={`${compact ? "size-4" : "size-5"} fill-none stroke-current stroke-[1.5]`}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={icon} />
        </svg>
      </span>
      <p className={`font-semibold tracking-snug text-primary ${compact ? "text-sm" : "text-md"}`}>{title}</p>
      {body && (
        <p className={`mt-1 max-w-[34ch] text-secondary ${compact ? "text-xs leading-relaxed" : "text-sm"}`}>{body}</p>
      )}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

/** Icons for the empty states, on the navigation's 16px grid. */
export const EMPTY_ICON = {
  /** A microphone: nothing recorded yet. */
  record: "M8 2.5a2 2 0 0 1 2 2v3a2 2 0 1 1-4 0v-3a2 2 0 0 1 2-2ZM4 7.5a4 4 0 0 0 8 0M8 11.5v2",
  /** A page with lines: no summary. */
  page: "M4 2.5h5.5l2.5 2.5v8.5H4zM6 7h4M6 9.5h4M6 12h2.5",
  /** A magnifier: nothing matched. */
  search: "M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z",
  /** A tick: nothing to do. */
  check: "M3 8.5 6.2 11.6 13 4.8",
  /** Speech lines: no transcript. */
  transcript: "M2.5 4h11M2.5 7h8M2.5 10h11M2.5 13h6",
  /** A warning triangle: could not load. */
  error: "M8 2.5 14 13H2zM8 6.5v3M8 11.2v.3",
} as const;

/** The quiet button an empty state offers, the same weight as the bar's secondary actions. */
export const EMPTY_BUTTON =
  "inline-flex h-7 items-center gap-1.5 rounded-md bg-raised px-2.5 text-xs font-medium text-primary shadow-[var(--shadow-ring),var(--shadow-sm)] hover:bg-a-200 active:bg-a-300";
