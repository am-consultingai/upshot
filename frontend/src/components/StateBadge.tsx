import { useI18n } from "../i18n";
import Badge, { type BadgeTone } from "./Badge";
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
const LOUD = new Set(["RECORDING", "FAILED"]);

const TONE: Record<string, BadgeTone> = {
  RECORDING: "bad",
  FAILED: "bad",
  RENDERED: "good",
  DELIVERED: "good",
};

export default function StateBadge({ state }: { state: string }) {
  const { t } = useI18n();
  const key = `state.${state}` as MessageKey;
  const loud = LOUD.has(state);
  return (
    <Badge
      data-testid="state-badge"
      data-state={state}
      tone={TONE[state] ?? "neutral"}
      quiet={!loud}
      dot
    >
      {t(key)}
    </Badge>
  );
}
