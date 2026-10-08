import type { HTMLAttributes, ReactNode } from "react";

/**
 * What a banner is saying, which is all that changes between them.
 *
 * recording: the microphone is on (the recording bar).
 * attention: something wants a decision or is wrong: a meeting nobody is recording, the
 *   call that just ended, the app that stopped answering.
 * notice: a question that can wait: new Terms, the crash-report answer.
 */
export type BannerTone = "recording" | "attention" | "notice";

const TONE: Record<BannerTone, string> = {
  recording: "border-danger bg-danger-quiet",
  attention: "border-warning bg-warning-quiet",
  notice: "border-line bg-surface-2",
};

/**
 * The strip under the window's top edge that says something about the whole app, on
 * every page: the recording bar, the detection nudge, the offline notice and the notices.
 *
 * They were separate implementations that had drifted (different paddings, different
 * button heights, links in one and buttons in the next). One frame now: the same place,
 * the same height for a line of text and `sm` buttons, and the same entrance, a short
 * fade and drop on the motion tokens that `prefers-reduced-motion` turns off (`.ma-banner`
 * in index.css). The children are the line itself; `actions` sit at the inline end.
 */
export default function Banner({
  tone,
  actions,
  className = "",
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { tone: BannerTone; actions?: ReactNode }) {
  return (
    <div {...rest} data-tone={tone} className={`ma-banner border-b ${TONE[tone]} ${className}`}>
      <div className="mx-auto flex min-h-12 max-w-5xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 text-sm">
        {children}
        {actions && <div className="ms-auto flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
