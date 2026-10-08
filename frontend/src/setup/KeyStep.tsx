import { useEffect, useRef, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import Button, { Spinner, buttonClass } from "../components/Button";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import type { KeyProviderId } from "./flow";
import { Note, StepFrame, fill } from "./ui";

/**
 * Setup: an API key, offered only when no subscription was signed in on the step before
 * (product owner, 2026-09-28). A subscription that works makes this step disappear; one
 * ticked and left unfinished, or none ticked at all, brings it here. Skipping it is a
 * choice too: meetings are then transcribed and stop there (D63).
 */
export default function KeyStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useI18n();
  const { key } = useSetupSnapshot();
  return (
    <StepFrame
      id="key"
      title="firstRun.key.stepTitle"
      lead={<p data-testid="key-lead">{t("firstRun.key.stepLead")}</p>}
      footer={
        <>
          {key.phase === "valid" ? (
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
      <KeyOffer />
    </StepFrame>
  );
}

/** The one key setup offers: Gemini's, free with any Google account. Other providers'
 *  keys are entered in Settings (product owner, 2026-09-28). */
const GEMINI = { id: "gemini" as KeyProviderId, label: "assistant.provider.gemini" as MessageKey, console: "https://aistudio.google.com/apikey" };

/** A key looks pasted, not half-typed, once it is this long. */
const KEY_MIN = 20;

/**
 * No subscription is not the end of summaries.
 *
 * Gemini leads because anyone with a Google account can have a key in a minute at no
 * cost, and the button goes straight to the page that makes one. The key is checked the
 * moment it is pasted — there is no Check button to find. The free tier's terms let
 * Google use what is sent, and a meeting transcript is exactly what someone may not
 * want used, so the step says so beside the button rather than in a link.
 */
function KeyOffer() {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const { key } = useSetupSnapshot();
  const [value, setValue] = useState("");
  const provider = GEMINI;
  const providerName = t(provider.label);

  // Typed rather than pasted: check once the typing stops.
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const change = (next: string) => {
    setValue(next);
    clearTimeout(timer.current);
    if (next.trim().length >= KEY_MIN) timer.current = setTimeout(() => backend.checkKey(provider.id, next), 700);
  };

  return (
    <section data-testid="key-offer" className="rounded-xl bg-raised px-5 py-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-medium">{t("firstRun.key.title")}</h2>
          <p className="text-xs text-tertiary">{t("firstRun.key.lead")}</p>
        </div>
        <a
          data-testid="key-get-gemini"
          href={GEMINI.console}
          target="_blank"
          rel="noreferrer"
          className={buttonClass("primary", "md")}
          onClick={() => backend.chooseKeyProvider("gemini")}
        >
          {t("firstRun.key.getGemini")}
        </a>
      </div>

      <form
        className="mt-4"
        onSubmit={(event) => {
          event.preventDefault();
          backend.checkKey(provider.id, value);
        }}
      >
        <label htmlFor="setup-key" className="mb-1 block text-xs text-tertiary">
          {fill(t("firstRun.key.paste"), { provider: providerName })}
        </label>
        <div className="relative">
          <input
            id="setup-key"
            data-testid="key-input"
            type="password"
            dir="ltr"
            value={value}
            placeholder={t("firstRun.key.placeholder")}
            onChange={(event) => change(event.target.value)}
            onPaste={(event) => {
              const pasted = event.clipboardData.getData("text").trim();
              if (!pasted) return;
              event.preventDefault();
              clearTimeout(timer.current);
              setValue(pasted);
              backend.checkKey(provider.id, pasted);
            }}
            autoComplete="off"
            spellCheck={false}
            className={`h-10 w-full rounded-md bg-surface-2 px-3 font-mono text-sm text-primary ring-inset ${
              key.phase === "valid" ? "ring-2 ring-success" : key.phase === "invalid" ? "ring-2 ring-danger" : ""
            }`}
          />
          {key.phase === "checking" && (
            <span className="absolute end-3 top-1/2 -translate-y-1/2">
              <Spinner className="text-accent" />
            </span>
          )}
        </div>
      </form>

      <div className="mt-2 space-y-1">
        {key.phase === "checking" && <Note tone="neutral">{t("firstRun.key.checking")}</Note>}
        {key.phase === "valid" && (
          <Note tone="good" testId="key-valid">{fill(t("firstRun.key.valid"), { provider: providerName })}</Note>
        )}
        {key.phase === "invalid" && <Note tone="bad" testId="key-invalid">{t("firstRun.key.invalid")}</Note>}
        <p className="text-xs text-tertiary">{t("firstRun.key.stored")}</p>
      </div>

      <p data-testid="key-free-tier" className="mt-3 rounded-md bg-warning-quiet px-3 py-2 text-xs">
        {t("firstRun.key.freeTier")}{" "}
        <a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noreferrer" className="underline">
          {t("firstRun.key.terms")}
        </a>
      </p>
    </section>
  );
}
