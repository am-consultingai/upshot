import { cloneElement, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";

/**
 * A tooltip: a name, and optionally a sentence saying what the thing is *for*.
 *
 * Two jobs. On an icon-only control it names it ("Search · Ctrl K"). On a feature a new
 * user would not guess at — "Up next", "Ask this meeting", the open-items balloon — it
 * says in one line what the feature does. The second kind is what the Settings checkbox
 * turns off, for someone who has learned the app; with it off this renders the control
 * alone. (A control's accessible name never depended on it.)
 *
 * No wrapper element: the handlers are put on the control itself, so a tooltip can be
 * attached to a row, a link or a flex child without changing its layout. The bubble is
 * portalled and fixed, so a scroller or a sidebar cannot clip it, and it flips above
 * the control when there is no room below.
 *
 * Shape is measured, not invented: 24px for a label, 4px 8px, radius 4, a dark fill and
 * **no shadow**, entering over 140ms on Radix's exponential-out curve. The delay group
 * is what matters: the first tooltip in a row waits; the next appears at once, and the
 * group goes cold shortly after you leave, so a row of icons does not stutter.
 */
let groupWarm = false;
let coolTimer: number | undefined;

type Side = "top" | "bottom" | "end";

export default function Tooltip({
  label,
  hint,
  keys,
  side = "bottom",
  children,
}: {
  label: string;
  /** One sentence on what the feature is for. */
  hint?: string;
  keys?: string;
  side?: Side;
  children: React.ReactElement<Record<string, unknown>>;
}) {
  const { tooltips } = useI18n();
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const bubble = useRef<HTMLSpanElement | null>(null);
  const id = useId();

  useLayoutEffect(() => {
    if (!anchor || !bubble.current) return;
    const { offsetWidth: width, offsetHeight: height } = bubble.current;
    const rtl = document.documentElement.dir === "rtl";
    let left: number;
    let top: number;
    if (side === "end") {
      left = rtl ? anchor.left - width - 8 : anchor.right + 8;
      top = anchor.top + anchor.height / 2 - height / 2;
    } else {
      left = anchor.left + anchor.width / 2 - width / 2;
      const below = anchor.bottom + 6;
      const above = anchor.top - height - 6;
      top = side === "top" ? (above >= 8 ? above : below) : below + height > window.innerHeight - 8 ? above : below;
    }
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
    setPlace({ left, top });
  }, [anchor, side]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (!tooltips || !isValidElement(children)) return children;

  const show = (target: HTMLElement) => {
    window.clearTimeout(timer.current);
    const open = () => setAnchor(target.getBoundingClientRect());
    if (groupWarm) open();
    else timer.current = window.setTimeout(open, hint ? 350 : 500);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    setAnchor(null);
    setPlace(null);
    groupWarm = true;
    window.clearTimeout(coolTimer);
    coolTimer = window.setTimeout(() => {
      groupWarm = false;
    }, 300);
  };
  const chain =
    (own: unknown, next: (event: React.SyntheticEvent<HTMLElement>) => void) =>
    (event: React.SyntheticEvent<HTMLElement>) => {
      if (typeof own === "function") (own as (e: React.SyntheticEvent<HTMLElement>) => void)(event);
      next(event);
    };
  const props = children.props;

  return (
    <>
      {cloneElement(children, {
        onMouseEnter: chain(props.onMouseEnter, (event) => show(event.currentTarget)),
        onMouseLeave: chain(props.onMouseLeave, hide),
        onFocus: chain(props.onFocus, (event) => {
          if ((event.currentTarget as HTMLElement).matches(":focus-visible")) show(event.currentTarget);
        }),
        onBlur: chain(props.onBlur, hide),
        onPointerDown: chain(props.onPointerDown, hide),
        "aria-describedby": anchor ? id : props["aria-describedby"],
      })}
      {anchor &&
        createPortal(
          <span
            ref={bubble}
            id={id}
            role="tooltip"
            data-testid="tooltip"
            data-open=""
            className={`ma-tip pointer-events-none fixed z-[90] rounded-xs px-2 py-1 text-xs leading-4 ${
              hint ? "max-w-[260px]" : "whitespace-nowrap"
            }`}
            style={{ left: place?.left ?? -9999, top: place?.top ?? -9999 }}
          >
            <span className="font-medium">{label}</span>
            {keys && <kbd className="ms-1.5 opacity-60">{keys}</kbd>}
            {hint && <span className="mt-0.5 block font-normal opacity-80">{hint}</span>}
          </span>,
          document.body,
        )}
    </>
  );
}
