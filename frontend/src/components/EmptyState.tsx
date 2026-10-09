import type { ReactNode } from "react";
import { Check, FileText, Mic, Search, TextAlignStart, TriangleAlert, type LucideIcon } from "lucide-react";
import { buttonClass } from "./Button";
import { Icon } from "./Icon";

/**
 * What a screen says when it has nothing to show, or could not show it.
 *
 * A grey sentence used to do this everywhere, and a grey sentence answers "is it
 * broken?" and nothing else. Every empty state now carries the same three parts: a
 * mark, a line that names the situation, and a line that says what happens next —
 * with the one control that makes it happen, where there is one. The mark is a
 * Lucide glyph, like the navigation icons.
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
  /** One of `EMPTY_ICON`, or any other Lucide glyph. */
  icon: LucideIcon;
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
        <Icon icon={icon} className={compact ? "size-4" : "size-5"} />
      </span>
      <p className={`font-semibold tracking-snug text-primary ${compact ? "text-sm" : "text-md"}`}>{title}</p>
      {body && (
        <p className={`mt-1 max-w-[34ch] text-secondary ${compact ? "text-xs leading-relaxed" : "text-sm"}`}>{body}</p>
      )}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

/** Icons for the empty states. */
export const EMPTY_ICON = {
  /** A microphone: nothing recorded yet. */
  record: Mic,
  /** A page with lines: no summary. */
  page: FileText,
  /** A magnifier: nothing matched. */
  search: Search,
  /** A tick: nothing to do. */
  check: Check,
  /** Speech lines: no transcript. */
  transcript: TextAlignStart,
  /** A warning triangle: could not load. */
  error: TriangleAlert,
} satisfies Record<string, LucideIcon>;

/** The quiet button an empty state offers, the same weight as the bar's secondary actions. */
export const EMPTY_BUTTON = buttonClass("secondary");
