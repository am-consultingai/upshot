import { useEffect, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

/**
 * A destructive confirmation, replacing `window.confirm`.
 *
 * The browser's own box said "127.0.0.1:8078 says…" above the question, could not
 * name the meeting in bold, and put OK — the destructive answer — where the eye
 * lands. This names what is lost and where it lives ("on this computer only"), which
 * is the fact that makes deleting here different from deleting in a hosted tool.
 *
 * Shaped the way the mock measured it: no ×, no outside-click dismissal (a stray
 * click must not answer a question about deleting something), Cancel as a ghost, the
 * destructive action as a pale-red fill with red text rather than saturated red, and
 * a 10% scrim with a 4px blur instead of heavy black. Escape cancels; focus starts
 * on Cancel.
 */
interface Request {
  title: string;
  body: React.ReactNode;
  confirm: string;
  cancel: string;
  resolve: (answer: boolean) => void;
}

let pending: Request | null = null;
const listeners = new Set<() => void>();

function set(next: Request | null) {
  pending = next;
  for (const listener of listeners) listener();
}

export function confirmDialog(options: Omit<Request, "resolve">): Promise<boolean> {
  return new Promise((resolve) => {
    pending?.resolve(false);
    set({ ...options, resolve });
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export default function ConfirmHost() {
  const request = useSyncExternalStore(subscribe, () => pending);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const restore = useRef<Element | null>(null);

  const answer = (value: boolean) => {
    request?.resolve(value);
    set(null);
    if (restore.current instanceof HTMLElement) restore.current.focus();
  };

  useEffect(() => {
    if (!request) return undefined;
    restore.current = document.activeElement;
    cancelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        answer(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [request]);

  if (!request) return null;
  return createPortal(
    <div
      className="fixed inset-0 z-[80] grid place-items-center bg-scrim-soft px-4 backdrop-blur-[4px]"
      data-testid="confirm-backdrop"
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        data-testid="confirm-dialog"
        className="ma-dialog w-full max-w-[384px] rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <h2 id="confirm-title" className="text-md font-semibold tracking-snug">
          {request.title}
        </h2>
        <div className="mt-2 text-sm leading-relaxed text-secondary">{request.body}</div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            data-testid="confirm-cancel"
            onClick={() => answer(false)}
            className="h-7 rounded-md px-2.5 text-sm font-medium text-primary hover:bg-a-200 active:bg-a-300"
          >
            {request.cancel}
          </button>
          <button
            type="button"
            data-testid="confirm-ok"
            onClick={() => answer(true)}
            className="h-7 rounded-md bg-danger-quiet px-2.5 text-sm font-medium text-danger hover:brightness-95 active:translate-y-px"
          >
            {request.confirm}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
