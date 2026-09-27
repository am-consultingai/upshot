import { useI18n, type MessageKey } from "../i18n";
import { useSetupSnapshot } from "./backend";
import { cliUsable } from "./flow";
import { VendorLogo, type VendorId } from "./logos";
import { Badge, PRIMARY, QUIET, StepFrame } from "./ui";

const VENDORS: { id: VendorId; name: MessageKey; plan: MessageKey }[] = [
  { id: "claude", name: "firstRun.services.claude.name", plan: "firstRun.services.claude.plan" },
  { id: "codex", name: "firstRun.services.codex.name", plan: "firstRun.services.codex.plan" },
  { id: "gemini", name: "firstRun.services.gemini.name", plan: "firstRun.services.gemini.plan" },
];

/**
 * Setup: which AI services the user already pays for (D75). Nothing is installed or
 * opened here: the choice only says what the next step sets up, and what Upshot uses the
 * service for is said first. "None of these" passes the next step over.
 */
export default function ServicesStep({
  chosen,
  onChange,
  onNext,
  onBack,
}: {
  chosen: Record<VendorId, boolean>;
  onChange: (next: Record<VendorId, boolean>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const snapshot = useSetupSnapshot();
  const any = chosen.claude || chosen.codex || chosen.gemini;
  const ready = (id: VendorId) =>
    id !== "gemini" && snapshot.cliKnown && cliUsable(snapshot.cli[id]);

  return (
    <StepFrame
      id="services"
      title="firstRun.services.title"
      lead={<p data-testid="services-lead">{t("firstRun.services.lead")}</p>}
      footer={
        <>
          {any ? (
            <button type="button" data-testid="setup-next" className={PRIMARY} onClick={onNext}>
              {t("firstRun.continue")}
            </button>
          ) : (
            <button type="button" data-testid="setup-skip" className={QUIET} onClick={onNext}>
              {t("firstRun.services.none")}
            </button>
          )}
          <button type="button" data-testid="setup-back" className={QUIET} onClick={onBack}>
            {t("firstRun.back")}
          </button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-3">
        {VENDORS.map((vendor) => {
          const on = chosen[vendor.id];
          return (
            <button
              key={vendor.id}
              type="button"
              role="checkbox"
              aria-checked={on}
              data-testid={`service-${vendor.id}`}
              onClick={() => onChange({ ...chosen, [vendor.id]: !on })}
              className={`relative flex flex-col items-center gap-2 rounded-xl bg-raised px-4 pb-4 pt-5 text-center shadow-sm ring-inset hover:bg-a-100 ${
                on ? "ring-2 ring-accent" : "ring-1 ring-line-subtle"
              }`}
            >
              <span
                aria-hidden="true"
                className={`absolute start-3 top-3 grid size-5 place-items-center rounded-md text-xs ${
                  on ? "bg-accent text-on-accent" : "ring-1 ring-line-strong"
                }`}
              >
                {on ? "✓" : ""}
              </span>
              {ready(vendor.id) && (
                <span className="absolute end-3 top-3">
                  <Badge tone="good">{t("firstRun.ai.ready")}</Badge>
                </span>
              )}
              <VendorLogo vendor={vendor.id} size={40} />
              <span className="text-base font-medium">{t(vendor.name)}</span>
              <span className="text-xs leading-snug text-tertiary">{t(vendor.plan)}</span>
            </button>
          );
        })}
      </div>
      <p data-testid="services-next" className="mt-4 text-center text-sm text-secondary">
        {t("firstRun.services.next")}
      </p>
    </StepFrame>
  );
}
