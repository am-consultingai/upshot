import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";

const TONE: Record<string, string> = {
  RECORDING: "bg-danger-quiet text-danger",
  FAILED: "bg-danger-quiet text-danger",
  RENDERED: "bg-success-quiet text-success",
  DELIVERED: "bg-success-quiet text-success",
};

export default function StateBadge({ state }: { state: string }) {
  const { t } = useI18n();
  const key = `state.${state}` as MessageKey;
  return (
    <span
      data-testid="state-badge"
      data-state={state}
      className={`rounded px-2 py-0.5 text-xs ${TONE[state] ?? "bg-surface-3 text-secondary"}`}
    >
      {t(key)}
    </span>
  );
}
