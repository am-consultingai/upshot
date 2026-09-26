import { useCallback, useEffect, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import AiStep from "./AiStep";
import CalendarStep from "./CalendarStep";
import { AudioStep, CaptureStep, DoneStep, WelcomeStep } from "./Steps";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import {
  DEFAULT_CAPTURE,
  chooseSummarizer,
  initialTicks,
  resumeAt,
  stepsFor,
  type CaptureMode,
  type CliId,
  type StepId,
} from "./flow";

const LABEL: Record<StepId, MessageKey> = {
  welcome: "firstRun.step.welcome",
  calendar: "firstRun.step.calendar",
  ai: "firstRun.step.ai",
  audio: "firstRun.step.audio",
  capture: "firstRun.step.capture",
  done: "firstRun.step.done",
};

/**
 * First-run setup: Welcome → Google Calendar → AI summaries → Sound check → Recording
 * → Done (epic z8tj1hb01k).
 *
 * Which steps appear is decided once, when setup opens. The step reached is saved as it
 * changes, so an app closed half-way reopens where it was left.
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

  const [steps] = useState(() => stepsFor({ calendarAvailable: snapshot.calendar.available }));
  const [capture, setCapture] = useState<CaptureMode>(DEFAULT_CAPTURE);
  const [step, setStep] = useState<StepId>(() => resumeAt(steps, startAt ?? snapshot.savedStep));
  // Said once, on the step setup reopened on, and gone as soon as the user moves.
  const [resumed, setResumed] = useState(() => !startAt && step !== steps[0]);
  // Pre-ticked from what already works — once that is known, and only if the user has
  // not ticked anything themselves by then.
  const [ticked, setTickedState] = useState<Record<CliId, boolean>>(() =>
    snapshot.cliKnown ? initialTicks(snapshot.cli) : { claude: false, codex: false },
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

  useEffect(() => backend.saveStep(step), [backend, step]);
  const [opened] = useState(step);
  useEffect(() => {
    if (step !== opened) setResumed(false);
  }, [step, opened]);

  const index = steps.indexOf(step);
  const next = useCallback(() => setStep((s) => steps[Math.min(steps.indexOf(s) + 1, steps.length - 1)]), [steps]);
  const back = useCallback(() => setStep((s) => steps[Math.max(steps.indexOf(s) - 1, 0)]), [steps]);

  const noneTicked = !ticked.claude && !ticked.codex;
  const summarizer = chooseSummarizer(
    ticked,
    snapshot.cli,
    noneTicked && snapshot.key.phase === "valid" ? snapshot.key.provider : null,
  );

  const finish = async () => {
    setFinishing(true);
    try {
      await backend.finish({ summarizer, capture });
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
      <header className="flex shrink-0 flex-col items-center gap-3 px-6 pt-8 pb-2">
      <nav aria-label={t("firstRun.progress")}>
        <ol className="flex flex-wrap items-center justify-center gap-x-1 gap-y-2 text-xs">
          {steps.map((id, i) => {
            const state = i < index ? "done" : i === index ? "current" : "todo";
            return (
              <li key={id} className="flex items-center gap-1">
                {i > 0 && <span aria-hidden="true" className="h-px w-5 bg-line" />}
                <button
                  type="button"
                  data-testid={`setup-crumb-${id}`}
                  aria-current={state === "current" ? "step" : undefined}
                  disabled={state === "todo"}
                  onClick={() => setStep(id)}
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
                    className={`grid size-4 place-items-center rounded-full text-[10px] tabular-nums ${
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
        {/* Setup reads in English or Hebrew; the choice stays the app's language. */}
        <div role="radiogroup" aria-label={t("settings.language")} className="flex h-8 rounded-full bg-surface-2 p-0.5 text-xs">
          {(["en", "he"] as const).map((language) => (
            <button
              key={language}
              type="button"
              role="radio"
              aria-checked={locale === language}
              data-testid={`setup-language-${language}`}
              lang={language}
              onClick={() => {
                setLocale(language);
                backend.setLanguage(language);
              }}
              className={`h-7 rounded-full px-3 leading-7 ${locale === language ? "bg-raised font-medium shadow-sm" : "text-secondary hover:text-primary"}`}
            >
              {language === "en" ? t("firstRun.languageEnglish") : t("firstRun.languageHebrew")}
            </button>
          ))}
        </div>
        {/* A line kept for it whether it shows or not, so it cannot push the step down. */}
        <p data-testid={resumed ? "setup-resumed" : undefined} className="h-4 text-xs leading-4 text-secondary">
          {resumed ? t("firstRun.resumed") : ""}
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-6 pt-4">
      {step === "welcome" && <WelcomeStep onNext={next} />}
      {step === "calendar" && <CalendarStep onNext={next} onBack={back} />}
      {step === "ai" && <AiStep ticked={ticked} setTicked={setTicked} onNext={next} onBack={back} />}
      {step === "audio" && <AudioStep onNext={next} onBack={back} />}
      {step === "capture" && <CaptureStep mode={capture} onChange={setCapture} onNext={next} onBack={back} />}
      {step === "done" && (
        <DoneStep
          summarizer={summarizer}
          capture={capture}
          finishing={finishing}
          onFinish={() => void finish()}
          onBack={back}
        />
      )}
      </div>
      </div>
    </div>
  );
}
