import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Overflow and context menus, in CSS.
 *
 * Same argument as Tooltip: `@starting-style` plus `allow-discrete` handle enter
 * and exit, so this needs no dependency. What a library *would* add — a focus
 * scope, typeahead — is real and is the reason to revisit `@base-ui/react` later.
 * Esc, outside-click and arrow movement are here because a menu without them is
 * broken, not because they are the hard part.
 *
 * The surface is portalled to the body and positioned with `fixed`. It used to be
 * an absolutely positioned child of its trigger, which was fine on the meeting bar
 * and useless everywhere else: the sidebar's list and the inbox scroll, and a menu
 * inside a scroller is clipped by it. Portalling is also what lets the same surface
 * open at the pointer for a right-click.
 *
 * Anatomy is measured: 180-200px wide, 4px of padding around the list, 28px rows at
 * 8px radius, shortcuts trailing in muted 12px, and a **full-bleed separator above
 * the destructive item** — the piece Geist leaves out, which is what makes Delete
 * easy to mis-click.
 */
export interface MenuItem {
  id: string;
  label: string;
  keys?: string;
  danger?: boolean;
  separated?: boolean;
  run: () => void;
}

/** Where a surface opens: a point, and which of its edges sits on that point. */
export interface MenuAnchor {
  x: number;
  y: number;
  /** "end" puts the menu's inline-end edge at x — under a trailing trigger. */
  align: "start" | "end";
}

const WIDTH = 196;

export function MenuSurface({
  items,
  at,
  onClose,
  testid = "overflow-menu",
}: {
  items: MenuItem[];
  at: MenuAnchor;
  onClose: (refocus: boolean) => void;
  testid?: string;
}) {
  const [active, setActive] = useState(0);
  const surface = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);

  /*
   * Kept on screen. The side it opens toward is logical — in Hebrew a trailing
   * trigger's menu grows rightward — and it flips above the point when there is
   * no room below, which is what a row near the foot of the list needs.
   */
  useLayoutEffect(() => {
    const node = surface.current;
    const height = node?.offsetHeight ?? 0;
    const rtl = document.documentElement.dir === "rtl";
    const growsLeft = (at.align === "end") !== rtl;
    let left = growsLeft ? at.x - WIDTH : at.x;
    left = Math.max(8, Math.min(left, window.innerWidth - WIDTH - 8));
    let top = at.y;
    if (top + height > window.innerHeight - 8) top = Math.max(8, at.y - height - 8);
    setPlace({ left, top });
  }, [at]);

  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      if (!surface.current?.contains(event.target as Node)) onClose(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose(true);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((index) => (index + 1) % items.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((index) => (index - 1 + items.length) % items.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const item = items[active];
        onClose(false);
        item?.run();
      }
    };
    // Pointerdown, not click: a menu should close on the press, as every
    // implementation in the field does, or the click lands on what is underneath.
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    // A menu is anchored to a point on screen; once the page moves it is anchored
    // to nothing. Not in the first moments, though: opening one near the edge of a
    // scroller can itself scroll it into view, and that must not close it again.
    const opened = Date.now();
    const onScroll = () => {
      if (Date.now() - opened > 250) onClose(false);
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [items, active, onClose]);

  return createPortal(
    <div
      ref={surface}
      role="menu"
      data-testid={testid}
      data-open=""
      className="ma-menu fixed z-[60] rounded-lg bg-raised p-1 shadow-[var(--shadow-ring),var(--shadow-md),var(--shadow-edge)]"
      style={{
        width: WIDTH,
        left: place?.left ?? -9999,
        top: place?.top ?? -9999,
      }}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item, index) => (
        <div key={item.id}>
          {/* Full-bleed, negative-margined out of the list's own padding. */}
          {item.separated && <div className="-mx-1 my-1 h-px bg-line" />}
          <button
            type="button"
            role="menuitem"
            data-testid="overflow-menu-item"
            data-item={item.id}
            data-danger={item.danger ? "true" : undefined}
            data-active={index === active ? "true" : undefined}
            onPointerEnter={() => setActive(index)}
            onClick={() => {
              onClose(false);
              item.run();
            }}
            className={`flex h-7 w-full items-center gap-2 rounded-md px-2 text-start text-sm ${
              item.danger ? "text-danger" : "text-primary"
            } ${index === active ? "bg-a-200" : ""}`}
          >
            <span className="min-w-0 flex-1 truncate">{item.label}</span>
            {item.keys && <kbd className="ms-auto font-mono text-2xs text-tertiary">{item.keys}</kbd>}
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}

/** The `⋯` trigger and its menu. */
export default function Menu({
  label,
  items,
  testid = "overflow-menu-trigger",
  small = false,
  trigger,
  triggerClassName,
}: {
  label: string;
  items: MenuItem[];
  testid?: string;
  /** 22px, for a row's reserved slot rather than a toolbar. */
  small?: boolean;
  /** A trigger that is not `⋯` — a chip that opens its own choices. */
  trigger?: React.ReactNode;
  triggerClassName?: string;
}) {
  const [at, setAt] = useState<MenuAnchor | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);

  const openAtTrigger = () => {
    const rect = button.current?.getBoundingClientRect();
    if (!rect) return;
    const rtl = document.documentElement.dir === "rtl";
    setAt({ x: rtl ? rect.left : rect.right, y: rect.bottom + 4, align: "end" });
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        data-testid={testid}
        aria-haspopup="menu"
        aria-expanded={at !== null}
        aria-label={trigger ? undefined : label}
        onClick={(event) => {
          // Inside a row that is itself a link: the menu, not the navigation.
          event.preventDefault();
          event.stopPropagation();
          if (at) setAt(null);
          else openAtTrigger();
        }}
        title={trigger ? undefined : label}
        className={
          triggerClassName ??
          `grid shrink-0 place-items-center text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300 ${
            small ? "size-5.5 rounded-xs" : "size-7 rounded-md"
          } ${at ? "bg-a-200 text-primary" : ""}`
        }
      >
        {trigger ?? (
          <svg viewBox="0 0 16 16" className={`${small ? "size-3.5" : "size-4"} fill-current`}>
            <circle cx="3.5" cy="8" r="1.3" />
            <circle cx="8" cy="8" r="1.3" />
            <circle cx="12.5" cy="8" r="1.3" />
          </svg>
        )}
      </button>
      {at && (
        <MenuSurface
          items={items}
          at={at}
          onClose={(refocus) => {
            setAt(null);
            if (refocus) button.current?.focus();
          }}
        />
      )}
    </>
  );
}

/**
 * A right-click menu for a row: spread `onContextMenu` on the row, render `menu`.
 *
 * The same items as the row's `⋯`, so the two can never disagree about what a row
 * can do. Shift+F10 and the context-menu key arrive as a `contextmenu` event with no
 * pointer position; those open under the row instead of at the corner of the screen.
 */
export function useContextMenu(items: MenuItem[]): {
  onContextMenu: (event: React.MouseEvent) => void;
  menu: React.ReactNode;
} {
  const [at, setAt] = useState<MenuAnchor | null>(null);
  const onContextMenu = (event: React.MouseEvent) => {
    event.preventDefault();
    if (event.clientX === 0 && event.clientY === 0) {
      const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
      setAt({ x: rect.left + 12, y: rect.bottom, align: "start" });
      return;
    }
    setAt({ x: event.clientX, y: event.clientY, align: "start" });
  };
  const menu = at ? (
    <MenuSurface items={items} at={at} testid="context-menu" onClose={() => setAt(null)} />
  ) : null;
  return { onContextMenu, menu };
}
