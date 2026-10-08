import type { ComponentPropsWithRef } from "react";

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
 * What a button is for, which decides how loud it is.
 *
 * primary: the one thing the surface is for; there is at most one per view.
 * secondary: a real action beside it, drawn as a hairline ring with no fill.
 * ghost: no ring until hovered, for Cancel and for the quieter half of a pair.
 * destructive: throws something away. Tinted rather than solid, so it never outshouts
 *   the primary next to it (the mock's delete confirmation).
 * record: the recording red, for Record and Stop only. It is the one solid red in the
 *   app, so it keeps meaning "the microphone" and is never borrowed for a warning.
 */
export type ButtonVariant = "primary" | "secondary" | "ghost" | "destructive" | "record";

/**
 * Two sizes, on the control-height tokens (tokens.css): `sm` (24px) for chrome — dialogs,
 * rails, banners, toasts, a page's header — and `md` (32px) beside the 32px inputs of
 * settings and setup. There were five heights before (24, 26, 28, 32 and a padded ~30).
 */
export type ButtonSize = "sm" | "md";

const SIZE: Record<ButtonSize, string> = {
  sm: "h-control-sm px-2.5 text-xs",
  md: "h-control px-3 text-sm",
};

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-on-accent shadow-[var(--shadow-sm),var(--shadow-edge)] hover:brightness-110 active:translate-y-px",
  secondary: "text-primary shadow-[var(--shadow-ring)] hover:bg-a-200 active:bg-a-300",
  ghost: "text-secondary hover:bg-a-200 hover:text-primary active:bg-a-300",
  destructive:
    "bg-danger-quiet text-danger shadow-[var(--shadow-ring-subtle)] hover:brightness-95 active:translate-y-px",
  record:
    "bg-danger text-on-solid shadow-[var(--shadow-sm),var(--shadow-edge)] hover:brightness-110 active:translate-y-px",
};

/** The classes alone, for the rare control that has to be a link rather than a button. */
export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "sm"): string {
  return `inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100 ${SIZE[size]} ${VARIANT[variant]}`;
}

/**
 * The app's button. `className` is for placement only (`ms-auto`, `w-full`, a margin):
 * colour, height and padding come from the variant and size, which is what keeps the
 * buttons of one screen the same height as each other.
 *
 * `busy` shows a spinner while the work the button started is still going. Busy is not
 * `disabled`: a disabled button is faded by its own classes, which would fade the spinner
 * too and make the one thing worth looking at the hardest to see. The click is swallowed
 * instead, so a second press cannot start the work twice.
 */
export default function Button({
  variant = "secondary",
  size = "sm",
  busy = false,
  onClick,
  className = "",
  children,
  ...rest
}: ComponentPropsWithRef<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      aria-busy={busy}
      aria-disabled={busy || Boolean(rest.disabled) || String(rest["aria-disabled"]) === "true"}
      data-busy={busy}
      onClick={busy ? undefined : onClick}
      className={`${buttonClass(variant, size)} ${busy ? "cursor-wait" : ""} ${className}`}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}
