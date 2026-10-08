import type { CSSProperties, ReactNode } from "react";
import { useI18n } from "../i18n";

/**
 * A grey bar where text or a control is about to be.
 *
 * "Loading…" told the reader that something was coming and nothing about what; a
 * skeleton shaped like the content says both, and the page does not jump when the
 * content arrives because it already has the content's shape. The shimmer lives in
 * `index.css` (`.ma-skeleton`) and stops under a reduced-motion preference: what is
 * left is a still bar, which still says "here, soon".
 */
export function Skeleton({ className = "", style }: { className?: string; style?: CSSProperties }) {
  return <span aria-hidden="true" className={`ma-skeleton ${className}`} style={style} />;
}

/**
 * The wrapper every skeleton sits in: one status for the screen reader, which hears
 * "Loading" once instead of a run of empty shapes, and a short wait before anything
 * is drawn, so a read that answers in a few milliseconds never flashes grey bars.
 */
export function Loading({
  children,
  className = "",
  testid = "loading",
}: {
  children: ReactNode;
  className?: string;
  testid?: string;
}) {
  const { t } = useI18n();
  return (
    <div data-testid={testid} role="status" aria-busy="true" className={`ma-loading ${className}`}>
      <span className="sr-only">{t("common.loading")}</span>
      {children}
    </div>
  );
}

/** Ragged widths, so a block of bars reads as prose rather than as a table. */
const PROSE = ["w-full", "w-[94%]", "w-[97%]", "w-[88%]", "w-[92%]", "w-[60%]"];

/** A paragraph, or a few: the summary, a transcript passage. */
export function SkeletonProse({ lines = 6, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2.5 ${className}`}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          className={`h-3 ${index === lines - 1 ? "w-[55%]" : PROSE[index % (PROSE.length - 1)]}`}
        />
      ))}
    </div>
  );
}

/** Rows of a list: an optional leading mark, a title line and a quieter line under it. */
export function SkeletonRows({
  rows = 4,
  lead,
  className = "",
  rowClassName = "py-2",
}: {
  rows?: number;
  /** The shape before the text: a status dot, a checkbox. */
  lead?: "dot" | "check";
  className?: string;
  rowClassName?: string;
}) {
  return (
    <div className={className}>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={`flex items-start gap-2.5 ${rowClassName}`}>
          {lead === "dot" && <Skeleton className="mt-1.5 size-1.5 shrink-0 rounded-full" />}
          {lead === "check" && <Skeleton className="size-[18px] shrink-0 rounded-full" />}
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className={`h-3 ${index % 3 === 1 ? "w-[58%]" : index % 3 === 2 ? "w-[72%]" : "w-[84%]"}`} />
            <Skeleton className="h-2 w-[38%]" />
          </div>
        </div>
      ))}
    </div>
  );
}
