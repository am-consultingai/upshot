import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useI18n } from "../i18n";
import SetupFlow from "./SetupFlow";
import { SetupBackendContext } from "./backend";
import type { SetupChoices, StepId } from "./flow";
import {
  MockSetupBackend,
  OUTCOME_CHOICES,
  PANEL,
  SCENARIOS,
  type Outcomes,
  type ScenarioId,
} from "./mockBackend";

/**
 * Setup 0 (z8tj1hb03a): the whole first-run flow on a scripted backend, for the user
 * to click through and confirm before any of it is wired up.
 *
 * Dev-only: the route exists in `vite dev` and in a build made with
 * VITE_SETUP_MOCK=1, never in the frozen app. Query parameters set it up without the
 * panel, which is how the browser specs drive it (language and theme are the saved
 * preferences, as everywhere else; the panel switches them too):
 *
 *   /setup-mock?scenario=both-ready&step=ai&panel=0
 */
export default function MockSetup() {
  const [params] = useSearchParams();
  const { setLocale, setTheme, locale, theme } = useI18n();
  const scenario = (params.get("scenario") as ScenarioId | null) ?? "fresh";
  const startAt = (params.get("step") as StepId | null) ?? undefined;
  const backend = useMemo(() => {
    const mock = new MockSetupBackend(scenario);
    if (!params.get("resume")) mock.reset(scenario);
    return mock;
    // One backend per page load; the panel resets it in place.
  }, []);
  const [generation, setGeneration] = useState(0);
  // What the last run through would have saved. Shown in the panel, not as a screen:
  // the real flow goes straight to the Timeline, so the mock starts over instead.
  const [finished, setFinished] = useState<SetupChoices | null>(null);

  useEffect(() => {
    const pace = Number(params.get("pace"));
    if (pace > 0) backend.pace = pace;
  }, []);

  const remount = () => setGeneration((g) => g + 1);

  return (
    <SetupBackendContext.Provider value={backend}>
      <div data-testid="setup-mock" data-finished={finished ? JSON.stringify(finished) : undefined} className="flex min-w-0 flex-1">
        <SetupFlow
          key={generation}
          startAt={generation === 0 ? startAt : undefined}
          onFinished={() => {
            setFinished(backend.finished);
            backend.reset(backend.scenario);
            remount();
          }}
        />
      </div>
      {params.get("panel") !== "0" && (
        <Panel
          backend={backend}
          locale={locale}
          theme={theme}
          onLocale={setLocale}
          onTheme={setTheme}
          onReset={(id) => {
            backend.reset(id);
            remount();
          }}
          onReopen={() => {
            backend.reopen();
            remount();
          }}
          finished={finished}
        />
      )}
    </SetupBackendContext.Provider>
  );
}

function Panel({
  backend,
  locale,
  theme,
  onLocale,
  onTheme,
  onReset,
  onReopen,
  finished,
}: {
  backend: MockSetupBackend;
  locale: "en" | "he";
  theme: string;
  onLocale: (locale: "en" | "he") => void;
  onTheme: (theme: "light" | "dark") => void;
  onReset: (scenario: ScenarioId) => void;
  onReopen: () => void;
  finished: SetupChoices | null;
}) {
  const [open, setOpen] = useState(true);
  const [outcomes, setOutcomes] = useState<Outcomes>(backend.outcomes);
  const select = "h-7 w-full rounded bg-surface-2 px-1.5 text-xs";

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-4 end-4 z-50 rounded-full bg-primary px-3 py-1.5 text-xs text-canvas shadow-lg"
      >
        {PANEL.show}
      </button>
    );
  }

  return (
    <aside
      dir="ltr"
      data-testid="setup-mock-panel"
      className="fixed bottom-4 end-4 z-50 w-72 space-y-2 rounded-lg bg-raised p-3 text-xs shadow-lg ring-1 ring-line"
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{PANEL.title}</span>
        <button type="button" className="text-tertiary hover:text-primary" onClick={() => setOpen(false)}>
          {PANEL.hide}
        </button>
      </div>
      {finished && (
        <p data-testid="setup-mock-last" className="rounded bg-surface-2 px-2 py-1 font-mono">
          {PANEL.finished} {finished.summarizer.provider}
          {finished.summarizer.fallback ? ` → ${finished.summarizer.fallback}` : ""} · {finished.capture}
        </p>
      )}
      <label className="block space-y-1">
        <span className="text-tertiary">{PANEL.scenario}</span>
        <select className={select} value={backend.scenario} onChange={(e) => onReset(e.target.value as ScenarioId)}>
          {SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-cols-2 gap-2">
        {(Object.keys(OUTCOME_CHOICES) as (keyof Outcomes)[]).map((key) => (
          <label key={key} className="block space-y-1">
            <span className="text-tertiary">{PANEL.outcome[key]}</span>
            <select
              className={select}
              value={outcomes[key]}
              onChange={(e) => {
                const next = { ...outcomes, [key]: e.target.value } as Outcomes;
                backend.outcomes = next;
                setOutcomes(next);
              }}
            >
              {OUTCOME_CHOICES[key].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5 pt-1">
        <button type="button" className="rounded bg-surface-2 px-2 py-1" onClick={() => onLocale(locale === "en" ? "he" : "en")}>
          {locale === "en" ? PANEL.hebrew : PANEL.english}
        </button>
        <button type="button" className="rounded bg-surface-2 px-2 py-1" onClick={() => onTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? PANEL.light : PANEL.dark}
        </button>
        <button type="button" className="rounded bg-surface-2 px-2 py-1" onClick={onReopen}>
          {PANEL.reopen}
        </button>
        <button type="button" className="rounded bg-surface-2 px-2 py-1" onClick={() => onReset(backend.scenario)}>
          {PANEL.startOver}
        </button>
      </div>
    </aside>
  );
}
