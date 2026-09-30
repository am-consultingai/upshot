import { useEffect, useRef } from "react";
import { useI18n } from "../i18n";
import BusyButton from "../components/BusyButton";
import { CopySignInLink } from "../components/CopySignInLink";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import { Note, PRIMARY, QUIET, SECONDARY, StepFrame, fill } from "./ui";
import { CalendarScene } from "./visuals";

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
 */
export default function CalendarStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const { calendar } = useSetupSnapshot();
  const { phase, accounts } = calendar;
  const connected = accounts.length > 0;

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
            <button type="button" data-testid="setup-next" className={PRIMARY} onClick={onNext}>
              {t("firstRun.continue")}
            </button>
          ) : (
            <button type="button" data-testid="setup-skip" className={QUIET} onClick={onNext}>
              {t("firstRun.skip")}
            </button>
          )}
          <button type="button" data-testid="setup-back" className={QUIET} onClick={onBack}>
            {t("firstRun.back")}
          </button>
        </>
      }
    >
      <div className="space-y-4 rounded-xl bg-raised px-5 py-5 shadow-sm">
        <CalendarScene phase={phase} account={accounts[0]?.address} />
        {!connected && (
          <p data-testid="calendar-how" className="text-center text-sm text-secondary">
            {t("firstRun.calendar.how")}
          </p>
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
              <BusyButton
                data-testid="calendar-add"
                busy={phase === "waiting"}
                className={QUIET}
                onClick={() => backend.connectCalendar()}
              >
                {t("firstRun.calendar.add")}
              </BusyButton>
              {phase === "waiting" && (
                <button
                  type="button"
                  data-testid="calendar-cancel"
                  className={QUIET}
                  onClick={() => backend.cancelCalendar()}
                >
                  {t("firstRun.calendar.cancel")}
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <BusyButton
              data-testid="calendar-connect"
              busy={phase === "waiting"}
              className={`${problem ? SECONDARY : PRIMARY} px-4 py-2`}
              onClick={() => backend.connectCalendar()}
            >
              {problem ? t("firstRun.calendar.retry") : t("firstRun.calendar.connect")}
            </BusyButton>
            {phase === "waiting" && (
              <button
                type="button"
                data-testid="calendar-cancel"
                className={QUIET}
                onClick={() => backend.cancelCalendar()}
              >
                {t("firstRun.calendar.cancel")}
              </button>
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
    </StepFrame>
  );
}
