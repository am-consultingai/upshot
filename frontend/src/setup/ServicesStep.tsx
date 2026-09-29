import { useI18n, type MessageKey } from "../i18n";
import { useSetupSnapshot } from "./backend";
import { CLI_IDS, cliUsable, type CliId } from "./flow";
import { VendorLogo } from "./logos";
import { Badge, PRIMARY, QUIET, StepFrame, fill } from "./ui";

const VENDORS: { id: CliId; name: MessageKey; plan: MessageKey }[] = [
  { id: "claude", name: "firstRun.services.claude.name", plan: "firstRun.services.claude.plan" },
  { id: "codex", name: "firstRun.services.codex.name", plan: "firstRun.services.codex.plan" },
  // Named for what the user pays for, a Google AI plan; the next step installs the
  // Antigravity CLI that spends it (D79).
  { id: "antigravity", name: "firstRun.services.gemini.name", plan: "firstRun.services.gemini.plan" },
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
  chosen: Record<CliId, boolean>;
  onChange: (next: Record<CliId, boolean>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t, locale } = useI18n();
  const snapshot = useSetupSnapshot();
  const any = CLI_IDS.some((id) => chosen[id]);
  const ready = (id: CliId) => snapshot.cliKnown && cliUsable(snapshot.cli[id]);
  // The button says what comes next: installing what was chosen and is not here yet
  // (product owner, 2026-09-28). Nothing left to install is a plain Continue.
  const toInstall = VENDORS.filter((v) => chosen[v.id] && !(snapshot.cliKnown && snapshot.cli[v.id].installed));
  const forward = toInstall.length
    ? fill(t("firstRun.services.install"), {
        names: new Intl.ListFormat(locale, { type: "conjunction" }).format(toInstall.map((v) => t(v.name))),
      })
    : t("firstRun.continue");

  return (
    <StepFrame
      id="services"
      title="firstRun.services.title"
      lead={<p data-testid="services-lead">{t("firstRun.services.lead")}</p>}
      footer={
        <>
          {any ? (
            <button type="button" data-testid="setup-next" className={PRIMARY} onClick={onNext}>
              {forward}
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
