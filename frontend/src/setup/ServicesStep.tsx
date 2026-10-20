import { useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { useSetupSnapshot } from "./backend";
import { CLI_IDS, cliUsable, summaryOrder, type CliId } from "./flow";
import { VendorLogo } from "./logos";
import { Badge, StepFrame, fill } from "./ui";
import Button from "../components/Button";

const VENDORS: { id: CliId; name: MessageKey; plan: MessageKey }[] = [
  { id: "claude", name: "firstRun.services.claude.name", plan: "firstRun.services.claude.plan" },
  { id: "codex", name: "firstRun.services.codex.name", plan: "firstRun.services.codex.plan" },
  // Named for what the user pays for, a Google AI plan; the next step installs the
  // Antigravity CLI that spends it (D79).
  { id: "antigravity", name: "firstRun.services.gemini.name", plan: "firstRun.services.gemini.plan" },
];
const NAME = Object.fromEntries(VENDORS.map((v) => [v.id, v.name])) as Record<CliId, MessageKey>;

/**
 * Setup: which AI plans the user pays for (D75). Nothing is installed or opened here.
 *
 * The question says "pay for" and the lead says a free account won't do, because free
 * accounts were ticked and only found out at sign-in (setup plan, 2026-10-20). Each
 * service is a row naming its plans. "I don't pay for any of these" is an answer of its
 * own, after an "or", and clears the ticks; Continue with no answer says so instead of
 * moving on. Once something is ticked, one line says what it means: which writes the
 * summaries and which takes over when its limit runs out (`chooseSummarizer`'s order).
 */
export default function ServicesStep({
  chosen,
  onChange,
  noPlan,
  onNoPlan,
  onNext,
  onBack,
}: {
  chosen: Record<CliId, boolean>;
  onChange: (next: Record<CliId, boolean>) => void;
  noPlan: boolean;
  onNoPlan: (none: boolean) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const snapshot = useSetupSnapshot();
  const order = summaryOrder(chosen);
  // Counted, not a flag: each Continue with no answer is announced again.
  const [unanswered, setUnanswered] = useState(0);
  const ready = (id: CliId) => snapshot.cliKnown && cliUsable(snapshot.cli[id]);
  const name = (id: CliId) => t(NAME[id]);

  const tick = (id: CliId) => {
    setUnanswered(0);
    onNoPlan(false);
    onChange({ ...chosen, [id]: !chosen[id] });
  };
  const none = () => {
    setUnanswered(0);
    onNoPlan(!noPlan);
    if (!noPlan) onChange(Object.fromEntries(CLI_IDS.map((id) => [id, false])) as Record<CliId, boolean>);
  };
  const forward = () => (order.length || noPlan ? onNext() : setUnanswered((n) => n + 1));

  const row = (on: boolean) =>
    `flex w-full items-center gap-3.5 rounded-xl bg-raised px-4 py-3.5 text-start shadow-sm ring-inset hover:bg-a-100 ${
      on ? "ring-2 ring-accent" : unanswered ? "ring-1 ring-danger" : "ring-1 ring-line-subtle"
    }`;

  return (
    <StepFrame
      id="services"
      title="firstRun.services.title"
      lead={<p data-testid="services-lead">{t("firstRun.services.lead")}</p>}
      footer={
        <>
          <Button data-testid="setup-next" variant="primary" size="md" onClick={forward}>
            {t("firstRun.continue")}
          </Button>
          <Button data-testid="setup-back" variant="ghost" size="md" onClick={onBack}>
            {t("firstRun.back")}
          </Button>
        </>
      }
    >
      {unanswered > 0 && (
        <p key={unanswered} id="services-unanswered" role="alert" data-testid="services-unanswered" className="mb-3 text-sm font-medium text-danger">
          {t("firstRun.services.required")}
        </p>
      )}
      <div
        role="group"
        aria-label={t("firstRun.services.title")}
        aria-describedby={unanswered > 0 ? "services-unanswered" : undefined}
        className="flex flex-col gap-2"
      >
        {VENDORS.map((vendor) => {
          const on = chosen[vendor.id];
          return (
            <button
              key={vendor.id}
              type="button"
              role="checkbox"
              aria-checked={on}
              data-testid={`service-${vendor.id}`}
              onClick={() => tick(vendor.id)}
              className={row(on)}
            >
              <span
                aria-hidden="true"
                className={`grid size-5 shrink-0 place-items-center rounded-md text-xs ${
                  on ? "bg-accent text-on-accent" : "ring-1 ring-line-strong"
                }`}
              >
                {on ? "✓" : ""}
              </span>
              <VendorLogo vendor={vendor.id} size={26} />
              <span className="min-w-0 flex-1">
                <span className="text-base font-medium">{t(vendor.name)}</span>
                <span className="text-sm text-secondary"> · {t(vendor.plan)}</span>
              </span>
              {ready(vendor.id) && <Badge tone="good">{t("firstRun.ai.ready")}</Badge>}
            </button>
          );
        })}
      </div>
      <div aria-hidden="true" className="my-3 flex items-center gap-3 text-xs text-tertiary">
        <span className="h-px flex-1 bg-line" />
        {t("firstRun.services.or")}
        <span className="h-px flex-1 bg-line" />
      </div>
      <button
        type="button"
        role="checkbox"
        aria-checked={noPlan}
        data-testid="services-none"
        onClick={none}
        className={row(noPlan)}
      >
        <span
          aria-hidden="true"
          className={`grid size-5 shrink-0 place-items-center rounded-md text-xs ${
            noPlan ? "bg-accent text-on-accent" : "ring-1 ring-line-strong"
          }`}
        >
          {noPlan ? "✓" : ""}
        </span>
        <span className="text-base">{t("firstRun.services.none")}</span>
      </button>
      {/* Always in the page, so a screen reader hears the line when it fills. */}
      <div role="status">
        {order.length > 0 && (
          <p data-testid="services-order" className="mt-4 rounded-xl bg-accent-quiet px-4 py-3 text-sm text-primary">
            {order.length === 1
              ? fill(t("firstRun.services.uses.one"), { first: name(order[0]) })
              : fill(t("firstRun.services.uses.two"), { first: name(order[0]), second: name(order[1]) })}
            {order[2] && <> {fill(t("firstRun.services.spare"), { name: name(order[2]) })}</>}
          </p>
        )}
      </div>
      <p data-testid="services-next" className="mt-4 text-center text-sm text-secondary">
        {t(noPlan ? "firstRun.services.nextNone" : "firstRun.services.next")}
      </p>
    </StepFrame>
  );
}
