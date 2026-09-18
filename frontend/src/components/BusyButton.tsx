import type { ButtonHTMLAttributes } from "react";

/** A ring that spins in the current text colour, sized to sit beside a button label. */
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      data-testid="spinner"
      aria-hidden="true"
      className={`inline-block size-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-e-transparent ${className}`}
    />
  );
}

/**
 * A button that shows a spinner while the work it started is still going.
 *
 * Busy is not `disabled`: a disabled button is faded by its own classes, which would fade
 * the spinner too and make the one thing worth looking at the hardest to see. The click is
 * swallowed instead, so a second press cannot start the work twice.
 */
export default function BusyButton({
  busy = false,
  onClick,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      aria-busy={busy}
      aria-disabled={busy || rest.disabled}
      data-busy={busy}
      onClick={busy ? undefined : onClick}
      className={`inline-flex items-center gap-1.5 ${busy ? "cursor-wait" : ""} ${className}`}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}
