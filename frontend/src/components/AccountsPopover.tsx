import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { accountColour } from "../lib/calendar";
import { ChevronDown } from "lucide-react";
import { Icon } from "./Icon";

export interface AccountChoice {
  id: string;
  label: string;
  /** The account's dot colour; null for a row with no account ("No calendar"). */
  color: number | null;
  checked: boolean;
}

/**
 * A list of calendar accounts with a checkbox each, the account's dot beside it: both
 * the switch and the legend (D82). It stays open while boxes are ticked — several are
 * often changed at once — and closes on Escape or a click outside.
 *
 * A dialog, not a menu: a menu closes on every pick. The calendar's letter shortcuts
 * pause while one is open, since Library.tsx skips them under any `[role=dialog]`.
 */
export default function AccountsPopover({
  anchor,
  title,
  choices,
  footnote,
  onToggle,
  onClose,
  testid,
}: {
  anchor: HTMLElement;
  title: string;
  choices: AccountChoice[];
  footnote?: ReactNode;
  onToggle: (id: string, checked: boolean) => void;
  onClose: () => void;
  testid: string;
}) {
  const surface = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);
  const WIDTH = 248;

  useLayoutEffect(() => {
    const rect = anchor.getBoundingClientRect();
    const height = surface.current?.offsetHeight ?? 160;
    const rtl = document.documentElement.dir === "rtl";
    let left = rtl ? rect.left : rect.right - WIDTH;
    left = Math.max(8, Math.min(left, window.innerWidth - WIDTH - 8));
    let top = rect.bottom + 6;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 6);
    setPlace({ left, top });
  }, [anchor, choices.length]);

  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      if (!surface.current?.contains(event.target as Node) && !anchor.contains(event.target as Node))
        onClose();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [anchor, onClose]);

  useEffect(() => {
    surface.current?.querySelector<HTMLElement>("input")?.focus();
  }, []);

  return createPortal(
    <div
      ref={surface}
      role="dialog"
      aria-label={title}
      data-testid={testid}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
        anchor.focus();
      }}
      className="ma-menu fixed z-[60] rounded-lg bg-raised p-1.5 shadow-[var(--shadow-ring),var(--shadow-md),var(--shadow-edge)]"
      data-open=""
      style={{ width: WIDTH, left: place?.left ?? -9999, top: place?.top ?? -9999 }}
    >
      <p className="px-2 pb-1 pt-0.5 text-2xs font-medium uppercase tracking-wide text-tertiary">
        {title}
      </p>
      {choices.map((choice) => (
        <label
          key={choice.id}
          data-testid={`${testid}-${choice.id}`}
          className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-a-200"
        >
          <input
            type="checkbox"
            checked={choice.checked}
            onChange={(event) => onToggle(choice.id, event.target.checked)}
            className="size-3.5 accent-[var(--accent)]"
          />
          <span
            aria-hidden
            className="inline-block size-2 flex-none rounded-full"
            style={{
              background: choice.color === null ? "transparent" : accountColour(choice.color),
              boxShadow: choice.color === null ? "inset 0 0 0 1px var(--line-strong)" : undefined,
            }}
          />
          <bdi className="min-w-0 truncate">{choice.label}</bdi>
        </label>
      ))}
      {footnote && <p className="px-2 pb-1 pt-1.5 text-2xs leading-snug text-tertiary">{footnote}</p>}
    </div>,
    document.body,
  );
}

/** A small chip that opens an AccountsPopover. */
export function AccountsTrigger({
  label,
  testid,
  active = false,
  children,
}: {
  label: string;
  testid: string;
  /** Something is filtered or hidden: the chip says so. */
  active?: boolean;
  children: (anchor: HTMLElement, close: () => void) => ReactNode;
}) {
  const button = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        ref={button}
        type="button"
        data-testid={testid}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className={`inline-flex h-control-sm items-center gap-1 rounded-sm px-2 text-xs shadow-[var(--shadow-ring)] hover:bg-a-200 hover:text-primary ${
          active ? "text-primary" : "text-secondary"
        }`}
      >
        {label}
        <Icon icon={ChevronDown} className="size-3" />
      </button>
      {open && button.current && children(button.current, () => setOpen(false))}
    </>
  );
}
