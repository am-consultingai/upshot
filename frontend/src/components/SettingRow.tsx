import { createContext, useContext, type ReactNode } from "react";
import { useI18n } from "../i18n";

/**
 * Config keys the environment is holding down, and the variable holding each.
 *
 * Settings provides it from `/api/settings`. It exists because the environment is the
 * top configuration layer: a launcher that exports `UP_DETECTION__MODE` beats
 * `app_config.json` on every start, so the control saved, read back correctly, and was
 * silently overridden again the next time the app opened — which looks exactly like a
 * screen that forgets. A control that is pinned now says so.
 */
export const PinnedContext = createContext<Record<string, string>>({});

/**
 * One setting: what it is on the left, the control that changes it on the right.
 *
 * This replaces a stacked form — a label, then a full-width bordered input, then
 * a note under it, repeated down the page. That shape belongs to a document you
 * fill in and submit, and it reads as one: the eye has to travel the full width
 * of the window to get from a name to its value, and every setting looks exactly
 * as important as every other.
 *
 * The pattern here is Microsoft's `SettingsCard`, which is the current Windows
 * idiom and what Linear and Vercel independently arrive at: a discrete surface
 * per setting, the header in full-strength text, the explanation directly under
 * it in the quiet tier, and the control right-aligned and only as wide as it
 * needs to be. No dividers — the cards are the separation.
 *
 * Below a narrow threshold the control wraps underneath the header rather than
 * being squeezed, which is also what `SettingsCard` does.
 */
export default function SettingRow({
  label,
  description,
  htmlFor,
  children,
  tone,
  configKey,
}: {
  label: string;
  description?: ReactNode;
  /** Points the label at its control, so clicking the name focuses the thing. */
  htmlFor?: string;
  children: ReactNode;
  /** A warning attached to this setting, shown under the description. */
  tone?: ReactNode;
  /** The dotted config key this row writes, so the row can say when it is pinned. */
  configKey?: string;
}) {
  const { t } = useI18n();
  const pinnedBy = useContext(PinnedContext)[configKey ?? ""];
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg bg-raised px-4 py-3 shadow-sm">
      <div className="min-w-[14rem] flex-1">
        {htmlFor ? (
          <label htmlFor={htmlFor} className="block text-sm font-medium">
            {label}
          </label>
        ) : (
          <span className="block text-sm font-medium">{label}</span>
        )}
        {description && (
          <p className="mt-0.5 max-w-prose text-xs leading-relaxed text-tertiary">
            {description}
          </p>
        )}
        {pinnedBy && (
          <p
            data-testid="setting-pinned"
            data-config-key={configKey}
            className="mt-1 max-w-prose text-xs leading-relaxed text-warning"
          >
            {t("settings.pinnedByEnv").replace("{var}", pinnedBy)}
          </p>
        )}
        {tone}
      </div>
      <div className="flex shrink-0 items-center gap-3">{children}</div>
    </div>
  );
}

/**
 * A group of settings. The heading is optional because a section of one group
 * already has the section's name at the top of the screen, and repeating it
 * immediately underneath is just noise.
 */
export function SettingGroup({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="mb-8">
      {title && <h2 className="mb-1.5 px-1 text-xs font-medium text-tertiary">{title}</h2>}
      {/*
       * Four pixels between cards, which is what the Windows sample stacks them
       * with: enough to read as separate surfaces, not enough to read as
       * separate sections.
       */}
      <div className="space-y-1">{children}</div>
    </section>
  );
}

/**
 * The shared look for a <select> in a settings row.
 *
 * A native select with the platform border and the platform arrow is the single
 * most dated control on a page, and the default `padding-right` leaves the arrow
 * crowding the text. This keeps the element native — it is the only control that
 * gets a real OS dropdown, correct keyboard handling and correct RTL for free —
 * and dresses it: the platform arrow is removed with `appearance: none` and
 * redrawn as a background chevron, on a recessed surface rather than inside a
 * drawn box.
 *
 * The chevron is inlined as a data URI on purpose: it has to change colour with
 * the theme, and `currentColor` does not reach a background image.
 */
export const SELECT_CLASS = [
  "h-8 min-w-[10rem] appearance-none rounded-md bg-surface-2 ps-2.5 pe-8 text-sm",
  "bg-[length:14px] bg-no-repeat",
  "bg-[position:right_0.5rem_center] rtl:bg-[position:left_0.5rem_center]",
  "bg-[image:var(--chevron)] hover:bg-a-200 active:bg-a-300",
].join(" ");
