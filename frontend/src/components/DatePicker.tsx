import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import { addDays, dayKey, startOfWeek } from "../lib/calendar";

/**
 * A due-date popover: a month, a today mark, the chosen day in accent.
 *
 * 212px at radius 10, anchored to the trigger's leading edge and flipped above it when
 * there is no room below — the numbers from the mock. The month chevrons mirror in
 * Hebrew, which is the one bug the reference implementation ships: "previous month"
 * points toward the start edge, and in a right-to-left page the start edge is on the
 * right.
 *
 * Arrow keys move a day or a week, PageUp/PageDown a month, Enter picks, Escape
 * closes — the grid pattern WAI-ARIA describes for a date picker.
 */
export default function DatePicker({
  value,
  anchor,
  onPick,
  onClose,
}: {
  /** YYYY-MM-DD, or null for none. */
  value: string | null;
  /** The trigger it opens from. */
  anchor: HTMLElement;
  onPick: (value: string | null) => void;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const initial = value ? new Date(`${value}T00:00:00`) : new Date();
  const [month, setMonth] = useState(() => new Date(initial.getFullYear(), initial.getMonth(), 1));
  const [focus, setFocus] = useState(() => dayKey(initial));
  const surface = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    const rect = anchor.getBoundingClientRect();
    const height = surface.current?.offsetHeight ?? 260;
    const rtl = document.documentElement.dir === "rtl";
    let left = rtl ? rect.right - 212 : rect.left;
    left = Math.max(8, Math.min(left, window.innerWidth - 220));
    let top = rect.bottom + 6;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 6);
    setPlace({ left, top });
  }, [anchor, month]);

  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      if (!surface.current?.contains(event.target as Node) && !anchor.contains(event.target as Node))
        onClose();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [anchor, onClose]);

  useEffect(() => {
    surface.current?.querySelector<HTMLElement>(`[data-day="${focus}"]`)?.focus();
  }, [focus, month]);

  const first = startOfWeek(month);
  const days = Array.from({ length: 42 }, (_, index) => addDays(first, index));
  const weekdays = Array.from({ length: 7 }, (_, index) =>
    new Intl.DateTimeFormat(locale, { weekday: "narrow" }).format(addDays(first, index)),
  );
  const today = dayKey(new Date());
  const shiftMonth = (by: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + by, 1));

  const onKeyDown = (event: React.KeyboardEvent) => {
    const at = new Date(`${focus}T00:00:00`);
    const rtl = document.documentElement.dir === "rtl";
    const step: Record<string, number> = {
      ArrowLeft: rtl ? 1 : -1,
      ArrowRight: rtl ? -1 : 1,
      ArrowUp: -7,
      ArrowDown: 7,
    };
    if (event.key in step) {
      event.preventDefault();
      const next = addDays(at, step[event.key]);
      setFocus(dayKey(next));
      if (next.getMonth() !== month.getMonth()) setMonth(new Date(next.getFullYear(), next.getMonth(), 1));
    } else if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      const by = event.key === "PageUp" ? -1 : 1;
      const next = new Date(at.getFullYear(), at.getMonth() + by, at.getDate());
      setFocus(dayKey(next));
      setMonth(new Date(next.getFullYear(), next.getMonth(), 1));
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      anchor.focus();
    }
  };

  return createPortal(
    <div
      ref={surface}
      role="dialog"
      aria-label={t("actions.pickDate")}
      data-testid="date-picker"
      onKeyDown={onKeyDown}
      className="ma-menu fixed z-[60] w-[212px] rounded-[10px] bg-raised p-2.5 shadow-[var(--shadow-ring),var(--shadow-md),var(--shadow-edge)]"
      data-open=""
      style={{ left: place?.left ?? -9999, top: place?.top ?? -9999 }}
    >
      <div className="mb-1.5 flex items-center gap-1">
        <span className="min-w-0 flex-1 truncate text-sm">
          <b className="font-semibold">
            {new Intl.DateTimeFormat(locale, { month: "long" }).format(month)}
          </b>{" "}
          <span className="text-tertiary">{month.getFullYear()}</span>
        </span>
        <button
          type="button"
          data-testid="date-picker-prev"
          aria-label={t("timeline.previous")}
          onClick={() => shiftMonth(-1)}
          className="grid size-6 place-items-center rounded-sm text-tertiary hover:bg-a-200 hover:text-primary"
        >
          <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5] rtl:-scale-x-100">
            <path d="M10 3.5 5.5 8l4.5 4.5" />
          </svg>
        </button>
        <button
          type="button"
          data-testid="date-picker-next"
          aria-label={t("timeline.next")}
          onClick={() => shiftMonth(1)}
          className="grid size-6 place-items-center rounded-sm text-tertiary hover:bg-a-200 hover:text-primary"
        >
          <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5] rtl:-scale-x-100">
            <path d="m6 3.5 4.5 4.5L6 12.5" />
          </svg>
        </button>
      </div>
      <div role="grid" className="grid grid-cols-7 gap-y-0.5 text-center">
        {weekdays.map((name, index) => (
          <span key={index} className="pb-1 text-[10px] uppercase text-tertiary">
            {name}
          </span>
        ))}
        {days.map((day) => {
          const key = dayKey(day);
          const inMonth = day.getMonth() === month.getMonth();
          const chosen = key === value;
          return (
            <button
              key={key}
              type="button"
              role="gridcell"
              data-day={key}
              data-testid="date-picker-day"
              aria-selected={chosen}
              tabIndex={key === focus ? 0 : -1}
              onClick={() => onPick(key)}
              className={`mx-auto grid size-[26px] place-items-center rounded-sm text-xs tabular-nums ${
                chosen
                  ? "bg-accent font-semibold text-on-accent"
                  : key === today
                    ? "bg-a-200 font-semibold text-primary"
                    : inMonth
                      ? "text-primary hover:bg-a-200"
                      : "text-tertiary opacity-60 hover:bg-a-200"
              }`}
            >
              {day.getDate()}
            </button>
          );
        })}
      </div>
      {value && (
        <button
          type="button"
          data-testid="date-picker-clear"
          onClick={() => onPick(null)}
          className="mt-2 h-6 w-full rounded-sm text-xs text-secondary hover:bg-a-200 hover:text-primary"
        >
          {t("actions.clearDate")}
        </button>
      )}
    </div>,
    document.body,
  );
}
