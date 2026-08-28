import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";

const TONE: Record<string, string> = {
  RECORDING: "bg-red-100 text-red-800",
  FAILED: "bg-red-100 text-red-800",
  NEEDS_REVIEW: "bg-amber-100 text-amber-900",
  RENDERED: "bg-green-100 text-green-800",
  DELIVERED: "bg-green-100 text-green-800",
};

export default function StateBadge({ state }: { state: string }) {
  const { t } = useI18n();
  const key = `state.${state}` as MessageKey;
  return (
    <span
      data-testid="state-badge"
      data-state={state}
      className={`rounded px-2 py-0.5 text-xs ${TONE[state] ?? "bg-neutral-200 text-neutral-700"}`}
    >
      {t(key)}
    </span>
  );
}
