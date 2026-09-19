import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";

/**
 * Colour is reserved for the states that need attention.
 *
 * Every row used to carry a filled pill, so "Ready" — which is the normal, boring,
 * overwhelmingly common state — shouted as loudly as "Failed". A list where
 * everything is highlighted has nothing highlighted. Ready and the other settled
 * states are now quiet text; recording and failure keep a tint, because those are
 * the two a glance down the list must catch.
 *
 * It also fixes a contrast bug: the filled green pill was dark green on a
 * near-black canvas in dark mode, effectively unreadable. The tints here are the
 * accent-style mixes, which move with the theme, and the dot carries the meaning
 * so the label is never the only signal.
 */
const TONE: Record<string, string> = {
  RECORDING: "bg-danger-quiet text-danger",
  FAILED: "bg-danger-quiet text-danger",
};

const DOT: Record<string, string> = {
  RECORDING: "bg-danger",
  FAILED: "bg-danger",
  RENDERED: "bg-success",
  DELIVERED: "bg-success",
};

export default function StateBadge({ state }: { state: string }) {
  const { t } = useI18n();
  const key = `state.${state}` as MessageKey;
  const tone = TONE[state];
  const dot = DOT[state] ?? "bg-line-strong";

  return (
    <span
      data-testid="state-badge"
      data-state={state}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full text-xs ${
        tone ? `${tone} px-2 py-0.5 font-medium` : "text-tertiary"
      }`}
    >
      <span className={`size-1.5 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
      {t(key)}
    </span>
  );
}
