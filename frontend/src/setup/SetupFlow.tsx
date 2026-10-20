import { useCallback, useEffect, useRef, useState } from "react";
import { LANGUAGES, useI18n, type Locale, type MessageKey } from "../i18n";
import AiStep from "./AiStep";
import CalendarStep from "./CalendarStep";
import FinishStep, { hasSummaryName, summaryName } from "./FinishStep";
import KeyStep from "./KeyStep";
import ServicesStep from "./ServicesStep";
import { AudioStep, CaptureStep, WelcomeStep } from "./Steps";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import {
  CLI_IDS,
  DEFAULT_CAPTURE,
  NO_CLIS,
  chooseSummarizer,
  crumbOf,
  finalSummarizer,
  initialTicks,
  isSubscription,
  resumeAt,
  stepsFor,
  withoutUnneeded,
  type CaptureMode,
  type CliId,
  type CrumbId,
  type KeyChoice,
  type StepId,
} from "./flow";

const LABEL: Record<CrumbId, MessageKey> = {
  welcome: "firstRun.step.welcome",
  plans: "firstRun.step.services",
  calendar: "firstRun.step.calendar",
  connect: "firstRun.step.ai",
  // The sound check and how to record: both about the recording (setup plan, 2026-10-20).
  recording: "firstRun.step.capture",
  finish: "firstRun.step.done",
};

/**
 * First-run setup: Welcome → AI plans → Google Calendar → Connect AI (or a key, when no
 * plan connected) → Sound check → how to record → Finish (epic z8tj1hb01k, D75, D79,
 * D105; setup plan, 2026-10-20).
 *
 * Which steps exist is decided once, when setup opens. Two of them follow the AI choices
 * as they change: Connect AI appears once a plan is ticked, and the key only while no
 * ticked plan is signed in (`withoutUnneeded`). The step reached is saved as it changes,
 * so an app closed half-way reopens where it was left; setup finished before reopens on
 * Finish, the review of what was chosen (D69).
 */
export default function SetupFlow({
  onFinished,
  startAt,
}: {
  onFinished: () => void;
  /** Open on this step instead of the saved one: the mock's `?step=` uses it. */
  startAt?: StepId;
}) {
  const { t, locale, setLocale } = useI18n();
  const backend = useSetupBackend();
  const snapshot = useSetupSnapshot();

  const [allSteps] = useState(() => stepsFor({ calendarAvailable: snapshot.calendar.available }));
  const { reopened } = snapshot.saved;
  // Reopened after an install, setup opens on what was chosen before (D69). Detection
  // switched off in Settings stays off unless a way to record is chosen here: null.
  const [capture, setCapture] = useState<CaptureMode | null>(
    snapshot.saved.capture ?? (snapshot.saved.detectionOff ? null : DEFAULT_CAPTURE),
  );
  // A key or local model set before is offered to keep, and chosen, on the no-plan step.
  const savedOwn = snapshot.saved.summarizer && !isSubscription(snapshot.saved.summarizer.provider)
    ? snapshot.saved.summarizer
    : null;
  const [keyChoice, setKeyChoice] = useState<KeyChoice | null>(savedOwn ? "keep" : null);
  // Crash reports, ticked unless the answer was no (decision Z8tj1hnz80).
  const [reports, setReports] = useState(snapshot.reports.consent !== "off");
  // "I don't pay for any of these": an answer, so the plans question can insist on one.
  const [noPlan, setNoPlan] = useState(false);
  const [step, setStep] = useState<StepId>(() =>
    resumeAt(allSteps, startAt ?? snapshot.savedStep ?? (reopened ? "finish" : null)),
  );
  // Said once, on the step setup was left on, and gone as soon as the user moves. Finish
  // after an install says "Welcome back" itself.
  const [resumed, setResumed] = useState(() => !startAt && step !== allSteps[0] && !!snapshot.savedStep);
  // Pre-ticked from what already works — once that is known, and only if the user has
  // not ticked anything themselves by then.
  const [ticked, setTickedState] = useState<Record<CliId, boolean>>(() =>
    snapshot.cliKnown ? initialTicks(snapshot.cli) : NO_CLIS,
  );
  const [touched, setTouched] = useState(false);
  const setTicked = useCallback((next: Record<CliId, boolean>) => {
    setTouched(true);
    setTickedState(next);
  }, []);
  useEffect(() => {
    if (snapshot.cliKnown && !touched) setTickedState(initialTicks(snapshot.cli));
    // Only when the facts arrive; later changes are the user's own doing.
  }, [snapshot.cliKnown]);
  const [finishing, setFinishing] = useState(false);
  // Which helpers were already here when the facts arrived, so a track does not show an
  // install that never happened — and does show one setup itself did.
  const [installedAtStart, setInstalledAtStart] = useState<Record<CliId, boolean> | null>(null);
  useEffect(() => {
    if (snapshot.cliKnown && !installedAtStart) {
      const { claude, codex, antigravity } = snapshot.cli;
      setInstalledAtStart({ claude: claude.installed, codex: codex.installed, antigravity: antigravity.installed });
    }
  }, [snapshot.cliKnown, snapshot.cli, installedAtStart]);

  // Each ticked plan's helper installs once the user moves on from the plans question, in
  // the background, while the calendar is connected (setup plan, O5). Once each: a
  // failed install waits for Try again on its sign-in screen.
  const started = useRef(new Set<CliId>());
  const pastPlans = allSteps.indexOf(step) > allSteps.indexOf("services");
  useEffect(() => {
    if (!pastPlans || !snapshot.cliKnown) return;
    for (const id of CLI_IDS) {
      const cli = snapshot.cli[id];
      if (ticked[id] && !cli.installed && cli.phase === "idle" && !started.current.has(id)) {
        started.current.add(id);
        backend.install(id);
      }
    }
  }, [pastPlans, snapshot.cliKnown, snapshot.cli, ticked, backend]);

  useEffect(() => backend.saveStep(step), [backend, step]);
  const [opened] = useState(step);
  useEffect(() => {
    if (step !== opened) setResumed(false);
  }, [step, opened]);

  // The steps as they stand now: whether Connect AI and API key are needed changes with
  // what is ticked and signed in. The step on screen stays in the walk even while it drops
  // out (a sign-in finishing under it), so Back and Continue still know where they are.
  const steps = withoutUnneeded(allSteps, ticked, snapshot.cli);
  const walk = allSteps.filter((s) => s === step || steps.includes(s));
  const index = walk.indexOf(step);
  // Which way the last move went: a step with screens of its own (Connect AI) opens on its
  // last one when entered with Back.
  const [cameBack, setCameBack] = useState(false);
  const next = () => {
    setCameBack(false);
    setStep(walk[Math.min(index + 1, walk.length - 1)]);
  };
  const back = () => {
    setCameBack(true);
    setStep(walk[Math.max(index - 1, 0)]);
  };
  // The track: each stop once. A stop reached earlier opens on its first screen.
  const crumbs = [...new Set(allSteps.map(crumbOf))];
  const crumbAt = crumbs.indexOf(crumbOf(step));
  const openCrumb = (crumb: CrumbId) => {
    const first = walk.find((s) => crumbOf(s) === crumb);
    if (first) setStep(first);
  };

  // A key counts only where the key step is offered: a subscription that works wins.
  // Ending on none keeps a key or local model chosen before, unless transcripts only was
  // the answer: passing over isn't removing.
  const summarizer = finalSummarizer(
    chooseSummarizer(
      ticked,
      snapshot.cli,
      keyChoice === "key" && snapshot.key.phase === "valid" ? snapshot.key.provider : null,
    ),
    snapshot.saved.summarizer,
    keyChoice,
  );

  const finish = async () => {
    setFinishing(true);
    try {
      await backend.finish({ summarizer, capture, reports: snapshot.reports.available ? reports : null });
      onFinished();
    } finally {
      setFinishing(false);
    }
  };

  return (
    /*
     * Three fixed zones, so nothing moves from step to step (the product owner's rule:
     * keep each component in the same box). The progress track and the language switch
     * sit at the top and never move; each step's title starts at the same line under
     * them; Continue and Back sit at the same place at the bottom (StepFrame). Only the
     * middle scrolls, when a step is taller than the window. Centred across, not down:
     * centring down re-centred the whole column whenever a step changed height.
     */
    <div data-testid="setup-flow" data-step={step} className="flex min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-col items-center gap-2 px-6 pt-5 pb-1 [@media(max-height:720px)]:pt-3">
      {/* The track and the language switch share a row: every line up here is a line
          the step below cannot use, and setup must fit the screen unscrolled. */}
      <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
      <nav aria-label={t("firstRun.progress")}>
        <ol className="flex flex-wrap items-center justify-center gap-x-1 gap-y-2 text-xs">
          {crumbs.map((id, i) => {
            const state = i < crumbAt ? "done" : i === crumbAt ? "current" : "todo";
            return (
              <li key={id} className="flex items-center gap-1">
                {i > 0 && <span aria-hidden="true" className="h-px w-5 bg-line" />}
                <button
                  type="button"
                  data-testid={`setup-crumb-${id}`}
                  aria-current={state === "current" ? "step" : undefined}
                  disabled={state === "todo"}
                  onClick={() => openCrumb(id)}
                  className={`flex h-7 items-center gap-1.5 rounded-full px-2 ${
                    state === "current"
                      ? "bg-accent-quiet font-medium text-primary"
                      : state === "done"
                        ? "text-secondary hover:bg-a-200"
                        : "text-tertiary"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`grid size-4 place-items-center rounded-full text-3xs tabular-nums ${
                      state === "todo" ? "ring-1 ring-line-strong" : "bg-accent text-on-accent"
                    }`}
                  >
                    {state === "done" ? "✓" : i + 1}
                  </span>
                  {t(LABEL[id])}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
        {/*
         * Setup reads in any of the five interface languages; the choice stays the app's
         * language. A select, not a row of buttons: five names do not fit beside the steps.
         */}
        <select
          aria-label={t("settings.language")}
          data-testid="setup-language"
          value={locale}
          onChange={(event) => {
            const language = event.target.value as Locale;
            setLocale(language);
            backend.setLanguage(language);
          }}
          className="h-8 rounded-full bg-surface-2 px-3 text-xs text-primary"
        >
          {LANGUAGES.map(({ code, name }) => (
            <option key={code} value={code} lang={code}>
              {name}
            </option>
          ))}
        </select>
      </div>
        {/* A line kept for it whether it shows or not, so it cannot push the step down. */}
        <p data-testid={resumed && !(reopened && step === "finish") ? "setup-resumed" : undefined} className="h-4 text-xs leading-4 text-secondary">
          {resumed && !(reopened && step === "finish") ? t("firstRun.resumed") : ""}
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-6 pt-4">
      {step === "welcome" && <WelcomeStep onNext={next} />}
      {step === "calendar" && <CalendarStep ticked={ticked} onNext={next} onBack={back} />}
      {step === "services" && (
        <ServicesStep
          chosen={ticked}
          onChange={setTicked}
          noPlan={noPlan}
          onNoPlan={setNoPlan}
          onNext={next}
          onBack={back}
        />
      )}
      {step === "ai" && (
        <AiStep ticked={ticked} installedAtStart={installedAtStart} cameBack={cameBack} onNext={next} onBack={back} />
      )}
      {step === "key" && (
        <KeyStep
          choice={keyChoice}
          onChoice={setKeyChoice}
          keep={savedOwn ? (hasSummaryName(savedOwn.provider) ? summaryName(savedOwn.provider, t) : "") : null}
          onNext={next}
          onBack={back}
        />
      )}
      {step === "audio" && <AudioStep onNext={next} onBack={back} />}
      {step === "capture" && <CaptureStep mode={capture} onChange={setCapture} onNext={next} onBack={back} />}
      {step === "finish" && (
        <FinishStep
          summarizer={summarizer}
          capture={capture}
          reports={reports}
          onReports={setReports}
          reopened={reopened}
          finishing={finishing}
          onChange={(target) => {
            setCameBack(false);
            setStep(target);
          }}
          onFinish={() => void finish()}
          onBack={back}
        />
      )}
      </div>
      </div>
    </div>
  );
}
