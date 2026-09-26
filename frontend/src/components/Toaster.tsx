import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

/**
 * Toasts: the one place something that happened *elsewhere* gets said.
 *
 * Kept for asynchronous results and for undo — "Summary finished", "Deleted", "Snoozed
 * until Monday · Undo". A synchronous result that is already visible where you clicked
 * (a copied link, a ticked box) gets no toast; saying it twice is noise.
 *
 * The deck is Sonner's, measured: 356px, bottom-trailing, newest in front. Collapsed,
 * the cards behind it hide their contents so the stack reads as one object; hovering
 * fans it out. Each card lives five seconds and the timer pauses while the pointer is
 * over the deck, because an undo you are reaching for must not leave under the cursor.
 */
export interface Toast {
  id: number;
  title: string;
  sub?: string;
  tone?: "neutral" | "danger";
  action?: { label: string; run: () => void };
}

let nextId = 1;
let toasts: Toast[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function toast(input: Omit<Toast, "id">): number {
  const id = nextId++;
  toasts = [{ ...input, id }, ...toasts].slice(0, 3);
  emit();
  return id;
}

export function dismissToast(id: number): void {
  toasts = toasts.filter((item) => item.id !== id);
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const LIFETIME_MS = 5000;

export default function Toaster() {
  const items = useSyncExternalStore(subscribe, () => toasts);
  const [hovered, setHovered] = useState(false);
  const timers = useRef(new Map<number, number>());

  useEffect(() => {
    const live = timers.current;
    if (hovered) {
      for (const timer of live.values()) window.clearTimeout(timer);
      live.clear();
      return;
    }
    for (const item of items) {
      if (live.has(item.id)) continue;
      live.set(
        item.id,
        window.setTimeout(() => {
          live.delete(item.id);
          dismissToast(item.id);
        }, LIFETIME_MS),
      );
    }
  }, [items, hovered]);

  if (items.length === 0) return null;
  return createPortal(
    <section
      data-testid="toaster"
      aria-live="polite"
      data-expanded={hovered ? "" : undefined}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      className="ma-toaster fixed bottom-6 z-[70] w-[356px]"
      // The assistant floats at the other corner (inline start), so nothing to avoid here.
      style={{ insetInlineEnd: 24, height: 72 }}
    >
      {items.map((item, index) => (
        <div
          key={item.id}
          data-testid="toast"
          data-index={index}
          role="status"
          className="ma-toast absolute inset-x-0 bottom-0 rounded-lg bg-raised p-4 text-sm shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
        >
          <div className="pe-20">
            <p className={`font-medium ${item.tone === "danger" ? "text-danger" : "text-primary"}`}>
              {item.title}
            </p>
            {item.sub && <p className="mt-0.5 text-xs text-tertiary">{item.sub}</p>}
          </div>
          {item.action && (
            <button
              type="button"
              data-testid="toast-action"
              onClick={() => {
                item.action?.run();
                dismissToast(item.id);
              }}
              className="absolute top-1/2 h-7 -translate-y-1/2 rounded-md px-2.5 text-xs font-medium text-primary shadow-[var(--shadow-ring)] hover:bg-a-200 active:bg-a-300"
              style={{ insetInlineEnd: 16 }}
            >
              {item.action.label}
            </button>
          )}
        </div>
      ))}
    </section>,
    document.body,
  );
}
