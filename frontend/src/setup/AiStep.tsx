import { useEffect, useRef, useState } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Spinner } from "../components/BusyButton";
import { useSetupBackend, useSetupSnapshot, type CliSnapshot } from "./backend";
import { CLI_IDS, chooseSummarizer, cliUsable, type CliId } from "./flow";
import { Badge, Note, PRIMARY, QUIET, SECONDARY, StepFrame, fill, type Tone } from "./ui";
import { VendorLogo } from "./logos";
import { StageTrack, VendorSignInScene } from "./visuals";

const NAME: Record<CliId, MessageKey> = {
  claude: "firstRun.ai.claude.name",
  codex: "firstRun.ai.codex.name",
  antigravity: "firstRun.ai.antigravity.name",
};
const INTRO: Record<CliId, MessageKey> = {
  claude: "firstRun.ai.intro.claude",
  codex: "firstRun.ai.intro.codex",
  antigravity: "firstRun.ai.intro.antigravity",
};
const BY: Record<CliId, MessageKey> = {
  claude: "firstRun.ai.claude.by",
  codex: "firstRun.ai.codex.by",
  antigravity: "firstRun.ai.antigravity.by",
};

function installedNow(cli: Record<CliId, CliSnapshot>): Record<CliId, boolean> {
  return { claude: cli.claude.installed, codex: cli.codex.installed, antigravity: cli.antigravity.installed };
}
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
 * Setup: connect the AI services chosen on the step before (D75).
 *
 * Each chosen subscription gets a card that works through it on its own: its helper
 * installs in the background at once, with no window; then the card says what signing
 * in involves, shows it, and waits for the user to press Sign in, which opens the
 * vendor's page in the browser once. Claude's and Antigravity's codes are pasted back
 * here. A card left unfinished does not stop anyone moving on; it just is not used, and
 * with none finished the next step offers an API key instead.
 */
export default function AiStep({
  ticked,
  onNext,
  onBack,
}: {
  ticked: Record<CliId, boolean>;
  onNext: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const snapshot = useSetupSnapshot();
  // Which CLIs were already here, so their track does not show an install that never
  // happened. Fixed when the facts arrive, not when an install later changes them.
  const [installedAtStart, setInstalledAtStart] = useState<Record<CliId, boolean> | null>(() =>
    snapshot.cliKnown ? installedNow(snapshot.cli) : null,
  );
  useEffect(() => {
    if (snapshot.cliKnown && !installedAtStart) setInstalledAtStart(installedNow(snapshot.cli));
  }, [snapshot.cliKnown, snapshot.cli, installedAtStart]);
  // A chosen service that is not installed starts installing as soon as this step opens,
  // once each: a failed install waits for Try again rather than starting over by itself.
  const started = useRef(new Set<CliId>());
  useEffect(() => {
    if (!snapshot.cliKnown) return;
    for (const id of CLI_IDS) {
      const cli = snapshot.cli[id];
      if (ticked[id] && !cli.installed && cli.phase === "idle" && !started.current.has(id)) {
        started.current.add(id);
        backend.install(id);
      }
    }
  }, [snapshot.cliKnown, snapshot.cli, ticked, backend]);

  const chosenCli = CLI_IDS.filter((id) => ticked[id]);
  const chosen = chooseSummarizer(ticked, snapshot.cli, null);
  const unfinished = chosenCli.filter((id) => {
    const cli = snapshot.cli[id];
    return !cliUsable(cli) && !BUSY.has(cli.phase) && !(cli.signedIn === true && cli.plan === "free");
  });
  // Moving on mid-install would leave a helper half there (product owner, 2026-09-28):
  // Continue is greyed out while any chosen one installs, and says why when pressed.
  const installing = chosenCli.some((id) => snapshot.cli[id].phase === "installing");
  const [waitNote, setWaitNote] = useState(false);
  useEffect(() => {
    if (!waitNote) return;
    const timer = setTimeout(() => setWaitNote(false), 4000);
    return () => clearTimeout(timer);
  }, [waitNote]);
  useEffect(() => {
    if (!installing) setWaitNote(false);
  }, [installing]);

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
          <span className="relative">
            {/* aria-disabled, not disabled: a disabled button never hears the press it
                has to answer. */}
            <button
              type="button"
              data-testid="setup-next"
              aria-disabled={installing}
              className={`${PRIMARY} ${installing ? "cursor-not-allowed opacity-50 hover:bg-accent" : ""}`}
              onClick={() => (installing ? setWaitNote(true) : onNext())}
            >
              {t("firstRun.continue")}
            </button>
            {waitNote && (
              <span
                role="status"
                data-testid="ai-wait-install"
                className="absolute bottom-full end-0 mb-2 w-max max-w-64 rounded-lg bg-raised px-3 py-2 text-sm shadow-md ring-1 ring-line-subtle"
              >
                {t("firstRun.ai.waitInstall")}
              </span>
            )}
          </span>
          <button type="button" data-testid="setup-back" className={QUIET} onClick={onBack}>
            {t("firstRun.back")}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        {chosenCli.map((id) => (
          <CliCard
            key={id}
            id={id}
            cli={snapshot.cli[id]}
            known={snapshot.cliKnown}
            skipInstall={installedAtStart?.[id] ?? false}
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
    </StepFrame>
  );
}

function CliCard({
  id,
  cli,
  known,
  skipInstall,
}: {
  id: CliId;
  cli: CliSnapshot;
  /** False while the machine is still being asked what is installed. */
  known: boolean;
  skipInstall: boolean;
}) {
  const { t } = useI18n();
  const badge = status(cli);
  return (
    <div data-testid={`ai-card-${id}`} className="rounded-xl bg-raised shadow-sm ring-1 ring-inset ring-line-subtle">
      <div className="flex items-center gap-3 px-4 py-3.5">
        <VendorLogo vendor={id} size={26} />
        <span className="min-w-0 flex-1">
          <span className="block text-base font-medium">{t(NAME[id])}</span>
          <span className="block text-xs text-tertiary">{t(BY[id])}</span>
        </span>
        {known ? <Badge tone={badge.tone}>{t(badge.label)}</Badge> : <Spinner className="text-tertiary" />}
      </div>
      <div className="space-y-3 border-t border-line-subtle px-4 pb-4 pt-3">
        <CliProgress id={id} cli={cli} skipInstall={skipInstall} />
      </div>
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
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
            <div className="su-indeterminate h-full w-1/3 rounded-full bg-accent" />
          </div>
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
  if (cli.installed) {
    // Installed and not signed in: say what signing in involves, show it, then let the
    // user start it, so the browser opening is expected rather than sprung on them.
    return (
      <div data-testid={`ai-signin-intro-${id}`} className="space-y-3">
        <StageTrack current="signin" skipInstall={skipInstall} />
        <p className="text-sm text-secondary">{t(INTRO[id])}</p>
        <VendorSignInScene vendor={id} />
        <button type="button" data-testid={`ai-signin-${id}`} className={PRIMARY} onClick={() => backend.signIn(id)}>
          {fill(t("firstRun.ai.signinButton"), { name })}
        </button>
      </div>
    );
  }
  // Not installed and not installing: the install was cancelled or never started.
  return (
    <button type="button" data-testid={`ai-resume-${id}`} className={PRIMARY} onClick={() => backend.install(id)}>
      {t("firstRun.ai.stage.install")}
    </button>
  );
}

/**
 * Sign-in. The vendor's tool has opened its page in the browser; the scene shows what
 * happens there. The link stays in reach in case the browser did not come forward, and
 * Claude's code is pasted back here, where Upshot passes it on (D75). Pasting it is
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
      <VendorSignInScene vendor={id} />
      {cli.signinNeedsCode ? (
        <form
          className="space-y-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            backend.submitCode(id, code);
          }}
        >
          <label htmlFor={`ai-code-${id}`} className="block text-sm">
            {t(id === "antigravity" ? "firstRun.ai.pasteCodeGoogle" : "firstRun.ai.pasteCodeHere")}
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
