import { useEffect, useRef } from "react";
import { useI18n, type MessageKey } from "../i18n";
import Button, { Spinner } from "../components/Button";
import { CopySignInLink } from "../components/CopySignInLink";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import { Note, StepFrame, fill } from "./ui";
import { CLI_IDS, type CliId } from "./flow";
import { CalendarScene, SceneFrame, useScene } from "./visuals";

const NAME: Record<CliId, MessageKey> = {
  claude: "firstRun.ai.claude.name",
  codex: "firstRun.ai.codex.name",
  antigravity: "firstRun.ai.antigravity.name",
};

/**
 * Setup 2: Google Calendar, encouraged and never required.
 *
 * The step moves on by itself once the first account is connected — the user's
 * attention is in the browser tab Google opened, and coming back to press Continue is a
 * step nobody needs. "Add another calendar" connects a second account beside it (D82);
 * after that the step stays, listing both, so the user can add a third or go on. Every
 * way a sign-in can end without an account (a permission unticked on Google's page, a
 * cancel, five minutes of nothing) says what happened and leaves both Try again and
 * Skip in reach.
 *
 * The scene of Google's pages rests on the consent screen, the one people stop at, and
 * plays once on "Show me"; the numbered steps say the same in words. While Google's page
 * is open the scene goes, so only the wait is on screen (setup plan, 2026-10-20).
 */
export default function CalendarStep({
  ticked,
  onNext,
  onBack,
}: {
  /** The plans chosen on the step before: their helpers install while this one is open. */
  ticked: Record<CliId, boolean>;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const { calendar, cli } = useSetupSnapshot();
  // Only helpers that install now, seen through to installed: one already here is news to
  // nobody. A ref filled during render: it only ever grows, and only from the snapshot.
  const seen = useRef(new Set<CliId>());
  for (const id of CLI_IDS) if (ticked[id] && cli[id].phase === "installing") seen.current.add(id);
  // A failed install says nothing here: its sign-in screen says so, with Try again.
  const installs = CLI_IDS.filter(
    (id) => seen.current.has(id) && (cli[id].phase === "installing" || cli[id].installed),
  );
  const { phase, accounts } = calendar;
  const connected = accounts.length > 0;
  // Google's consent screen is 46-76% of the 11-second clock (setup.css, "su-g-").
  const scene = useScene({ still: 6.8, length: 11, frames: [1, 3.5, 6.8, 9.5] });

  // Only the first connection made on this visit moves on: someone who comes Back to a
  // calendar that is already connected came back to look at it, and someone who adds
  // another calendar may want a third.
  const was = useRef(phase);
  const hadAccounts = useRef(connected);
  const next = useRef(onNext);
  next.current = onNext;
  useEffect(() => {
    const before = was.current;
    was.current = phase;
    if (before !== "waiting") hadAccounts.current = accounts.length > 0;
    if (before !== "waiting" || phase !== "connected" || hadAccounts.current) return;
    const timer = setTimeout(() => next.current(), 1500);
    return () => clearTimeout(timer);
  }, [phase, accounts.length]);

  const problem = phase === "partial" || phase === "cancelled" || phase === "timeout" || phase === "failed";
  const message = {
    partial: "firstRun.calendar.partial",
    cancelled: "firstRun.calendar.cancelled",
    timeout: "firstRun.calendar.timeout",
    failed: "firstRun.calendar.failed",
  } as const;

  return (
    <StepFrame
      id="calendar"
      title="firstRun.calendar.title"
      lead={<p>{t("firstRun.calendar.lead")}</p>}
      footer={
        <>
          {connected ? (
            <Button data-testid="setup-next" variant="primary" size="md" onClick={onNext}>
              {t("firstRun.continue")}
            </Button>
          ) : (
            <Button data-testid="setup-skip" variant="ghost" size="md" onClick={onNext}>
              {t("firstRun.skip")}
            </Button>
          )}
          <Button data-testid="setup-back" variant="ghost" size="md" onClick={onBack}>
            {t("firstRun.back")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 rounded-xl bg-raised px-5 py-5 shadow-sm">
        {connected ? (
          <CalendarScene phase={phase} account={accounts[0]?.address} />
        ) : (
          phase !== "waiting" && (
            <>
              <SceneFrame caption="firstRun.scene.preview" scene={scene}>
                <CalendarScene phase={phase} />
              </SceneFrame>
              <ol data-testid="calendar-how" className="list-inside list-decimal space-y-1 text-sm text-secondary">
                <li>{t("firstRun.calendar.step1")}</li>
                <li>{t("firstRun.calendar.step2")}</li>
                <li>{t("firstRun.calendar.step3")}</li>
              </ol>
            </>
          )
        )}
        {connected ? (
          <div className="space-y-3">
            <Note tone="good" testId="calendar-connected">
              {accounts.map((account) => (
                <span key={account.id} className="block" data-testid="calendar-connected-account">
                  {fill(t("firstRun.calendar.connectedAs"), { account: account.address })}
                </span>
              ))}
            </Note>
            {/* Another Google account: the same sign-in, with Google's account chooser.
                The accounts above stay connected whatever it comes to. */}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                data-testid="calendar-add"
                busy={phase === "waiting"}
                variant="ghost"
                size="md"
                onClick={() => backend.connectCalendar()}
              >
                {t("firstRun.calendar.add")}
              </Button>
              {phase === "waiting" && (
                <Button
                  data-testid="calendar-cancel"
                  variant="ghost"
                  size="md"
                  onClick={() => backend.cancelCalendar()}
                >
                  {t("firstRun.calendar.cancel")}
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              data-testid="calendar-connect"
              busy={phase === "waiting"}
              variant={problem ? "secondary" : "primary"} size="md"
              onClick={() => backend.connectCalendar()}
            >
              {problem ? t("firstRun.calendar.retry") : t("firstRun.calendar.connect")}
            </Button>
            {phase === "waiting" && (
              <Button
                data-testid="calendar-cancel"
                variant="ghost"
                size="md"
                onClick={() => backend.cancelCalendar()}
              >
                {t("firstRun.calendar.cancel")}
              </Button>
            )}
          </div>
        )}
        {phase === "waiting" && (
          <Note tone="neutral">
            {t("firstRun.calendar.waiting")} <CopySignInLink url={calendar.authUrl} />
          </Note>
        )}
        {problem && (
          <Note tone={phase === "partial" ? "warn" : "neutral"} testId={`calendar-${phase}`}>
            {t(message[phase])}
          </Note>
        )}
        <p className="max-w-prose text-xs leading-relaxed text-tertiary">{t("firstRun.calendar.privacy")}</p>
      </div>
      {installs.length > 0 && (
        // The AI helpers chosen a step ago, installing meanwhile: nothing to do, just seen.
        <ul data-testid="calendar-installs" className="mt-3 space-y-1 px-1 text-xs text-tertiary">
          {installs.map((id) => (
            <li key={id} data-testid={`calendar-install-${id}`} className="flex items-center gap-2">
              {cli[id].phase === "installing" ? (
                <>
                  <Spinner className="text-accent" />
                  {fill(t("firstRun.ai.installing"), { name: t(NAME[id]) })}
                </>
              ) : (
                <>
                  <span aria-hidden="true" className="text-success">✓</span>
                  {fill(t("firstRun.ai.installedBehind"), { name: t(NAME[id]) })}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </StepFrame>
  );
}
