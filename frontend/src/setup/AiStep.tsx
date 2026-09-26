import { useEffect, useRef, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Spinner } from "../components/BusyButton";
import { useSetupBackend, useSetupSnapshot, type CliSnapshot } from "./backend";
import { chooseSummarizer, cliUsable, type CliId, type KeyProviderId } from "./flow";
import { Badge, Note, PRIMARY, QUIET, SECONDARY, StepFrame, fill, type Tone } from "./ui";
import { InstallScene, SignInScene, StageTrack } from "./visuals";

const NAME: Record<CliId, MessageKey> = { claude: "firstRun.ai.claude.name", codex: "firstRun.ai.codex.name" };
const BY: Record<CliId, MessageKey> = { claude: "firstRun.ai.claude.by", codex: "firstRun.ai.codex.by" };
const BUSY = new Set(["installing", "signing-in", "testing"]);

/** The badge in a card's corner: what is on this machine, before anything is done. */
function status(cli: CliSnapshot): { tone: Tone; label: MessageKey } {
  if (!cli.installed) return { tone: "neutral", label: "firstRun.ai.notInstalled" };
  if (cli.signedIn === true && cli.plan === "free") return { tone: "warn", label: "firstRun.ai.freeBadge" };
  if (cli.signedIn === true) return { tone: "good", label: "firstRun.ai.ready" };
  if (cli.signedIn === false) return { tone: "neutral", label: "firstRun.ai.signedOut" };
  return { tone: "neutral", label: "firstRun.ai.unknown" };
}

/**
 * Setup 3: the AI that writes summaries, on a subscription the user already pays for.
 *
 * Ticking a card is the only click it takes: an absent CLI starts installing at once,
 * and sign-in follows the install by itself; an installed one goes straight to
 * sign-in. The only thing left to the user is the provider's own page (and, for
 * Claude, pasting its code back). Each stage is shown, not described.
 *
 * Ticking neither offers a key (Setup 6); a card started and abandoned does not stop
 * anyone moving on, it just does not count (Setup 8).
 */
export default function AiStep({
  ticked,
  setTicked,
  onNext,
  onBack,
}: {
  ticked: Record<CliId, boolean>;
  setTicked: (next: Record<CliId, boolean>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const snapshot = useSetupSnapshot();
  // Which CLIs were already here, so their track does not show an install that never
  // happened. Fixed when the facts arrive, not when an install later changes them.
  const [installedAtStart, setInstalledAtStart] = useState<Record<CliId, boolean> | null>(() =>
    snapshot.cliKnown ? { claude: snapshot.cli.claude.installed, codex: snapshot.cli.codex.installed } : null,
  );
  useEffect(() => {
    if (snapshot.cliKnown && !installedAtStart) {
      setInstalledAtStart({ claude: snapshot.cli.claude.installed, codex: snapshot.cli.codex.installed });
    }
  }, [snapshot.cliKnown, snapshot.cli, installedAtStart]);
  const noneTicked = !ticked.claude && !ticked.codex;
  const checkedKey = snapshot.key.phase === "valid" ? snapshot.key.provider : null;
  const chosen = chooseSummarizer(ticked, snapshot.cli, noneTicked ? checkedKey : null);
  const unfinished = (["claude", "codex"] as const).filter((id) => {
    const cli = snapshot.cli[id];
    return ticked[id] && !cliUsable(cli) && !BUSY.has(cli.phase) && !(cli.signedIn === true && cli.plan === "free");
  });

  const toggle = (id: CliId) => {
    const cli = snapshot.cli[id];
    if (ticked[id]) {
      if (cli.phase !== "idle") backend.cancel(id);
    } else if (cli.phase === "idle") {
      if (!cli.installed) backend.install(id);
      else if (cli.signedIn !== true) backend.signIn(id);
    }
    setTicked({ ...ticked, [id]: !ticked[id] });
  };

  return (
    <StepFrame
      id="ai"
      title="firstRun.ai.title"
      lead={
        <p data-testid="ai-notice" className="inline-flex items-start gap-2">
          <span
            aria-hidden="true"
            className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-accent text-[11px] font-bold text-on-accent"
          >
            i
          </span>
          {t("firstRun.ai.notice")}
        </p>
      }
      footer={
        <>
          {chosen.provider !== "none" || !noneTicked ? (
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
      <div className="grid items-start gap-3 sm:grid-cols-2">
        {(["claude", "codex"] as const).map((id) => (
          <CliCard
            key={id}
            id={id}
            cli={snapshot.cli[id]}
            ticked={ticked[id]}
            known={snapshot.cliKnown}
            skipInstall={installedAtStart?.[id] ?? false}
            onToggle={() => toggle(id)}
          />
        ))}
      </div>

      <div className="mt-3 space-y-1 px-1">
        {chosen.fallback && <Note tone="good" testId="ai-both">{t("firstRun.ai.both")}</Note>}
        {unfinished.map((id) => (
          <Note key={id} tone="neutral" testId={`ai-unfinished-${id}`}>
            {fill(t("firstRun.ai.unfinished"), { name: t(NAME[id]) })}
          </Note>
        ))}
      </div>

      {noneTicked && snapshot.cliKnown && <KeyOffer />}
    </StepFrame>
  );
}

function CliCard({
  id,
  cli,
  ticked,
  known,
  skipInstall,
  onToggle,
}: {
  id: CliId;
  cli: CliSnapshot;
  ticked: boolean;
  /** False while the machine is still being asked what is installed. */
  known: boolean;
  skipInstall: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const badge = status(cli);
  return (
    <div
      data-testid={`ai-card-${id}`}
      data-ticked={ticked}
      className={`rounded-xl bg-raised shadow-sm ring-inset ${ticked ? "ring-2 ring-accent" : "ring-1 ring-line-subtle"}`}
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={ticked}
        aria-busy={!known}
        disabled={!known}
        data-testid={`ai-tick-${id}`}
        onClick={onToggle}
        className="flex w-full items-center gap-3 rounded-xl px-4 py-3.5 text-start hover:bg-a-100"
      >
        <span
          aria-hidden="true"
          className={`grid size-5 shrink-0 place-items-center rounded-md text-xs ${
            ticked ? "bg-accent text-on-accent" : "ring-1 ring-line-strong"
          }`}
        >
          {ticked ? "✓" : ""}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-base font-medium">{t(NAME[id])}</span>
          <span className="block text-xs text-tertiary">{t(BY[id])}</span>
        </span>
        {known ? <Badge tone={badge.tone}>{t(badge.label)}</Badge> : <Spinner className="text-tertiary" />}
      </button>
      {ticked && (
        <div className="space-y-3 border-t border-line-subtle px-4 pb-4 pt-3">
          <CliProgress id={id} cli={cli} skipInstall={skipInstall} />
        </div>
      )}
    </div>
  );
}

/** Where a ticked card is: the stage track, the scene for that stage, and one line. */
function CliProgress({ id, cli, skipInstall }: { id: CliId; cli: CliSnapshot; skipInstall: boolean }) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const name = t(NAME[id]);
  const account = cli.account ?? "";

  switch (cli.phase) {
    case "installing":
      return (
        <>
          <StageTrack current="install" />
          <InstallScene />
          <Busy testId={`ai-installing-${id}`}>{fill(t("firstRun.ai.installing"), { name })}</Busy>
        </>
      );
    case "install-failed":
      return (
        <>
          <StageTrack current="install" failed />
          <Note tone="bad" testId={`ai-install-failed-${id}`}>{t("firstRun.ai.installFailed")}</Note>
          <button type="button" className={SECONDARY} onClick={() => backend.install(id)}>
            {t("firstRun.ai.retry")}
          </button>
        </>
      );
    case "signing-in":
      return <SignIn id={id} cli={cli} skipInstall={skipInstall} />;
    case "signin-failed":
      return (
        <>
          <StageTrack current="signin" failed skipInstall={skipInstall} />
          <Note tone="bad" testId={`ai-signin-failed-${id}`}>{t("firstRun.ai.signinFailed")}</Note>
          <button type="button" className={SECONDARY} onClick={() => backend.signIn(id)}>
            {t("firstRun.ai.retry")}
          </button>
        </>
      );
    case "testing":
      return (
        <>
          <StageTrack current="signin" skipInstall={skipInstall} />
          <Busy testId={`ai-testing-${id}`}>{t("firstRun.ai.testing")}</Busy>
        </>
      );
    case "idle":
      break;
  }

  if (cli.signedIn === true && cli.plan === "free") {
    return (
      <>
        <Note tone="warn" testId={`ai-free-${id}`}>{fill(t("firstRun.ai.freePlan"), { account })}</Note>
        <button type="button" className={SECONDARY} onClick={() => backend.signIn(id)}>
          {t("firstRun.ai.otherAccount")}
        </button>
      </>
    );
  }
  if (cli.signedIn === true) {
    return (
      <>
        <StageTrack current="ready" skipInstall={skipInstall} />
        <Note tone="good" testId={`ai-ready-${id}`}>{fill(t("firstRun.ai.readyAs"), { account })}</Note>
      </>
    );
  }
  // Cancelled half-way: the same action again, one click.
  return (
    <button
      type="button"
      data-testid={`ai-resume-${id}`}
      className={PRIMARY}
      onClick={() => (cli.installed ? backend.signIn(id) : backend.install(id))}
    >
      {cli.installed ? t("firstRun.ai.stage.signin") : t("firstRun.ai.stage.install")}
    </button>
  );
}

/**
 * Sign-in. The backend has already opened the provider's page; the scene shows what
 * happens there. The link stays in reach in case the browser did not come forward, and
 * Claude's code is pasted back here — the one step no CLI lets us skip. Pasting it is
 * enough: there is no button to find afterwards.
 */
function SignIn({ id, cli, skipInstall }: { id: CliId; cli: CliSnapshot; skipInstall: boolean }) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const [code, setCode] = useState("");
  const [copied, setCopied] = useState(false);
  return (
    <div data-testid={`ai-signing-in-${id}`} className="space-y-3">
      <StageTrack current="signin" skipInstall={skipInstall} />
      <SignInScene code={cli.signinNeedsCode} intoWindow={cli.codeInWindow} />
      {cli.signinNeedsCode && cli.codeInWindow ? (
        <Busy>{t("firstRun.ai.pasteInWindow")}</Busy>
      ) : cli.signinNeedsCode ? (
        <form
          className="space-y-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            backend.submitCode(id, code);
          }}
        >
          <label htmlFor={`ai-code-${id}`} className="block text-sm">
            {t("firstRun.ai.signingIn")} {t("firstRun.ai.pasteCode")}
          </label>
          <div className="flex gap-2">
            <input
              id={`ai-code-${id}`}
              data-testid={`ai-code-${id}`}
              dir="ltr"
              value={code}
              placeholder={t("firstRun.ai.code")}
              onChange={(event) => setCode(event.target.value)}
              onPaste={(event) => {
                const pasted = event.clipboardData.getData("text").trim();
                if (!pasted) return;
                event.preventDefault();
                setCode(pasted);
                backend.submitCode(id, pasted);
              }}
              autoComplete="off"
              spellCheck={false}
              className="h-9 min-w-0 flex-1 rounded-md bg-surface-2 px-2.5 font-mono text-sm text-primary"
            />
            <button type="submit" data-testid={`ai-submit-code-${id}`} disabled={!code.trim()} className={PRIMARY}>
              {t("firstRun.ai.finishSignIn")}
            </button>
          </div>
        </form>
      ) : (
        <Busy>{t("firstRun.ai.signingIn")}</Busy>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {cli.signinUrl && (
          <>
            <a href={cli.signinUrl} target="_blank" rel="noreferrer" className="text-accent underline">
              {t("firstRun.ai.openPage")}
            </a>
            <button
              type="button"
              className="text-secondary underline"
              onClick={() => {
                void navigator.clipboard?.writeText(cli.signinUrl ?? "").catch(() => undefined);
                setCopied(true);
              }}
            >
              {copied ? t("firstRun.ai.copied") : t("firstRun.ai.copyLink")}
            </button>
          </>
        )}
        <button type="button" className="text-secondary underline" onClick={() => backend.cancel(id)}>
          {t("firstRun.ai.cancel")}
        </button>
      </div>
      <p className="text-xs text-tertiary">{t("firstRun.ai.neverSees")}</p>
    </div>
  );
}

function Busy({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <p data-testid={testId} className="flex items-center gap-2 text-sm text-secondary">
      <Spinner className="text-accent" />
      {children}
    </p>
  );
}

const KEY_PROVIDERS: { id: KeyProviderId; label: MessageKey; console: string }[] = [
  { id: "gemini", label: "assistant.provider.gemini", console: "https://aistudio.google.com/apikey" },
  { id: "anthropic", label: "assistant.provider.anthropic", console: "https://console.anthropic.com/settings/keys" },
  { id: "openai", label: "assistant.provider.openai", console: "https://platform.openai.com/api-keys" },
];

/** A key looks pasted, not half-typed, once it is this long. */
const KEY_MIN = 20;

/**
 * Setup 6: no subscription is not the end of summaries.
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
  const provider = KEY_PROVIDERS.find((p) => p.id === key.provider) ?? KEY_PROVIDERS[0];
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
    <section data-testid="key-offer" className="mt-6 rounded-xl bg-raised px-5 py-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-medium">{t("firstRun.key.title")}</h2>
          <p className="text-xs text-tertiary">{t("firstRun.key.lead")}</p>
        </div>
        <a
          data-testid="key-get-gemini"
          href={KEY_PROVIDERS[0].console}
          target="_blank"
          rel="noreferrer"
          className={PRIMARY}
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

      {provider.id === "gemini" && (
        <p data-testid="key-free-tier" className="mt-3 rounded-md bg-warning-quiet px-3 py-2 text-xs">
          {t("firstRun.key.freeTier")}{" "}
          <a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noreferrer" className="underline">
            {t("firstRun.key.terms")}
          </a>
        </p>
      )}

      <details className="mt-4" open={provider.id !== "gemini"}>
        <summary className="cursor-pointer text-xs font-medium text-secondary">{t("firstRun.key.other")}</summary>
        <div className="mt-2 flex flex-wrap items-center gap-2" role="radiogroup">
          {KEY_PROVIDERS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={p.id === provider.id}
              data-testid={`key-provider-${p.id}`}
              onClick={() => backend.chooseKeyProvider(p.id)}
              className={`rounded-md px-3 py-1.5 text-xs ${
                p.id === provider.id ? "bg-accent-quiet font-medium text-primary" : "bg-surface-2 text-secondary hover:bg-a-200"
              }`}
            >
              {t(p.label)}
            </button>
          ))}
          {provider.id !== "gemini" && (
            <a href={provider.console} target="_blank" rel="noreferrer" className="text-xs text-accent underline">
              {t("firstRun.key.console")}
            </a>
          )}
        </div>
      </details>
    </section>
  );
}
