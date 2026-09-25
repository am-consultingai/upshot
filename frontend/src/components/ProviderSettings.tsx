import { useContext, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type LlmProvider } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import BusyButton from "./BusyButton";
import { PinnedContext } from "./SettingRow";
import Tooltip from "./Tooltip";

/**
 * The words that differ between the two subscription CLIs. The flow is one flow —
 * install, sign in in a console of its own, watch for it to finish, test — and only
 * what it is called, whose sign-in page opens and what plan it needs are different.
 */
const CLI_COPY: Record<string, { missing: MessageKey; signIn: MessageKey; plan: MessageKey }> = {
  "claude-subscription": {
    missing: "settings.cliMissing",
    signIn: "settings.signInHint",
    plan: "settings.cliPlanNote",
  },
  "codex-subscription": {
    missing: "settings.codexMissing",
    signIn: "settings.codexSignInHint",
    plan: "settings.codexPlanNote",
  },
};

/** Which secret name each provider reads. `undefined` means it needs no key. */
const SECRET_FOR: Record<string, string | undefined> = {
  anthropic: "anthropic",
  openai: "openai",
  gemini: "gemini",
};

/** A provider that needs no key must not claim one is stored. */
function readyLabel(provider: LlmProvider): MessageKey {
  if (provider.needs === "cli") {
    if (!provider.ready) return "settings.cliNotInstalled";
    // Installed is not signed in, and neither is the same as "too old to say".
    if (provider.signed_in === true) return "settings.cliSignedIn";
    if (provider.signed_in === false) return "settings.cliNotSignedIn";
    return "settings.cliInstalled";
  }
  if (provider.needs === "key") return provider.ready ? "settings.keySet" : "settings.keyMissing";
  return "settings.localReady";
}

/** Green only when the provider would actually work right now. */
function readyTone(provider: LlmProvider): string {
  const good = provider.needs === "cli" ? provider.ready && provider.signed_in !== false : provider.ready;
  return good ? "bg-success-quiet text-success" : "bg-surface-3 text-secondary";
}

/**
 * How the providers are grouped on screen, in the order they appear.
 *
 * A subscription you already pay for costs nothing extra to use, so it goes first;
 * a key you have to create and fund goes second; a model on this machine is neither
 * and gets its own frame rather than being filed under one of them.
 */
const GROUPS = [
  {
    id: "subscription",
    label: "settings.groupSubscription",
    hint: "settings.groupSubscriptionHint",
    holds: (provider: LlmProvider) => provider.needs === "cli",
  },
  {
    id: "keys",
    label: "settings.groupApiKeys",
    hint: "settings.groupApiKeysHint",
    holds: (provider: LlmProvider) => provider.needs === "key",
  },
  {
    id: "local",
    label: "settings.groupLocalModel",
    hint: "settings.groupLocalModelHint",
    holds: (provider: LlmProvider) => provider.needs !== "cli" && provider.needs !== "key",
  },
] as const satisfies readonly {
  id: string;
  label: MessageKey;
  hint: MessageKey;
  holds: (provider: LlmProvider) => boolean;
}[];

/**
 * Whether the wait on an Install or Sign in window is over: the CLI reports signed in, or
 * a status fetched after the launch says the window is gone (the user closed it). An
 * older server that does not report the window leaves only the timeout to end it.
 */
export function watchFinished(state: {
  signedIn: boolean;
  consoleOpen: boolean | undefined;
  checkedAt: number;
  launchedAt: number;
}): boolean {
  if (state.signedIn) return true;
  return state.consoleOpen === false && state.checkedAt > state.launchedAt;
}

export default function ProviderSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [keys, setKeys] = useState<Record<string, string>>({});
  // The radio must respond to the click immediately, not after the server round-trip —
  // otherwise it visibly snaps back and the UI reads as broken.
  const [pendingProvider, setPendingProvider] = useState<string | null>(null);
  const [tested, setTested] = useState<Record<string, { ok: boolean; detail: string }>>({});
  // Installing and signing in both happen in a console of their own, minutes after the
  // request that launched them returns. Invalidating on that response asks the server
  // before anything has happened; the answer only changes later, so the row has to keep
  // looking until it does.
  // Which CLI's console is being waited on, or null.
  const [watching, setWatching] = useState<string | null>(null);
  // When the watched console was launched: a status fetched before then says nothing
  // about the window it opened.
  const since = useRef(0);
  const watch = (provider: string) => {
    since.current = Date.now();
    setWatching(provider);
  };

  const status = useQuery({
    queryKey: ["llm-status"],
    queryFn: api.llmStatus,
    refetchInterval: watching ? 3000 : false,
  });
  const invalidate = () => queryClient.invalidateQueries();

  const select = useMutation({
    mutationFn: (provider: string) => api.putSettings({ "llm.provider": provider }),
    onSuccess: invalidate,
    onSettled: () => setPendingProvider(null),
  });
  const saveKey = useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      api.putSecrets({ [name]: value }),
    onSuccess: invalidate,
  });
  const test = useMutation({
    mutationFn: (provider: string) => api.llmTest(provider),
    onSuccess: (result) =>
      setTested((previous) => ({
        ...previous,
        [result.provider]: { ok: result.ok, detail: result.error ?? result.model ?? "" },
      })),
  });
  const signin = useMutation({
    mutationFn: (provider: string) => api.llmSignin(provider),
    onSuccess: (result, provider) => {
      if (result.launched) watch(provider);
      invalidate();
    },
  });
  const signout = useMutation({
    mutationFn: (provider: string) => api.llmSignout(provider),
    onSuccess: invalidate,
  });
  const cancelSignin = useMutation({
    mutationFn: (provider: string) => api.llmSigninCancel(provider),
    onSuccess: () => {
      setWatching(null);
      invalidate();
    },
  });
  // Which row's sign-in link was just copied, for a moment's acknowledgement.
  const [copied, setCopied] = useState<string | null>(null);
  const update = useMutation({
    mutationFn: (provider: string) => api.llmUpdate(provider),
    onSuccess: invalidate,
  });
  const install = useMutation({
    mutationFn: (provider: string) => api.llmInstall(provider),
    onSuccess: (result, provider) => {
      // Nothing was launched only when no installer can run; then the guide is the answer.
      if (!result.launched && result.docs) window.open(result.docs, "_blank", "noreferrer");
      if (result.launched) watch(provider);
      invalidate();
    },
  });
  const fallback = useMutation({
    mutationFn: (provider: string) => api.putSettings({ "llm.fallback_provider": provider }),
    onSuccess: invalidate,
  });

  // Any of the three console-launching actions can fail server-side; silence would read
  // as an unresponsive button, which is precisely how the last one was reported. Each
  // row shows only the failure of its own CLI.

  // Signed in is the finish line, not installed. The Install button carries straight on
  // into the login, so stopping at "the binary exists" would stop watching in the middle
  // of the very step the user is still completing.
  const cli = (status.data?.providers ?? []).find((provider) => provider.id === watching);
  const signedIn = cli?.signed_in === true;
  const finished = watchFinished({
    signedIn,
    consoleOpen: cli?.console_open,
    checkedAt: status.dataUpdatedAt,
    launchedAt: since.current,
  });
  useEffect(() => {
    if (!watching) return undefined;
    if (finished) {
      setWatching(null);
      return undefined;
    }
    // Give up rather than poll for the rest of the session: the login may be abandoned,
    // and an old build can never report success however long we wait.
    const timer = window.setTimeout(() => setWatching(null), 180_000);
    return () => window.clearTimeout(timer);
  }, [watching, finished]);

  const active = pendingProvider ?? status.data?.active;
  /*
   * `llm.provider` can hold a value that is not one of the rows — "fake", which the
   * demo launcher pins, or a provider that has since been removed. Every radio then
   * renders unchecked and the screen silently claims nothing is selected, which is
   * the one thing it must not do: summaries *are* being produced by something.
   */
  const pinnedBy = useContext(PinnedContext)["llm.provider"];
  const unlisted =
    active && !(status.data?.providers ?? []).some((provider) => provider.id === active)
      ? active
      : null;

  return (
    <section data-testid="provider-settings" className="mt-6">
      <h2 className="mb-2 text-sm font-semibold text-tertiary">{t("settings.summaries")}</h2>
      {/* `data-unlisted-provider`, not `data-provider`: the specs select rows with
          `[data-provider="..."]` and a banner answering that selector would be a trap. */}
      {unlisted && (
        <p
          data-testid="provider-unlisted"
          data-unlisted-provider={unlisted}
          className="mb-2 max-w-prose rounded-md bg-warning-quiet px-3 py-2 text-xs leading-relaxed text-warning"
        >
          {t("settings.providerUnlisted").replace("{provider}", unlisted)}
          {pinnedBy && ` ${t("settings.pinnedByEnv").replace("{var}", pinnedBy)}`}
        </p>
      )}
      <div className="grid gap-4">
        {GROUPS.map((group) => {
          const members = (status.data?.providers ?? []).filter(group.holds);
          if (members.length === 0) return null;
          return (
            <fieldset
              key={group.id}
              data-testid="provider-group"
              data-group={group.id}
              className="rounded-lg border border-line px-3 pb-3"
            >
              {/* The group's explanation is a sentence, on the tooltip primitive. */}
              <Tooltip label={t(group.label)} hint={t(group.hint)}>
                <legend
                  data-testid="provider-group-label"
                  className="cursor-help px-1 text-xs font-medium text-secondary"
                >
                  {t(group.label)}
                </legend>
              </Tooltip>
              <div className="grid gap-2">
        {members.map((provider: LlmProvider) => {
          const secret = SECRET_FOR[provider.id];
          const result = tested[provider.id];
          const copy = CLI_COPY[provider.id] ?? CLI_COPY["claude-subscription"];
          const mine = watching === provider.id;
          const failure =
            (install.variables === provider.id && install.error) ||
            (signin.variables === provider.id && signin.error) ||
            (signout.variables === provider.id && signout.error) ||
            (update.variables === provider.id && update.error) ||
            null;
          return (
            <article
              key={provider.id}
              data-testid="provider-row"
              data-provider={provider.id}
              data-ready={provider.ready}
              data-signed-in={String(provider.signed_in)}
              data-active={provider.id === active}
              className={`rounded border p-3 ${
                provider.id === active ? "border-accent" : "border-line-subtle"
              } bg-raised`}
            >
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="llm-provider"
                  data-testid="provider-select"
                  checked={provider.id === active}
                  onChange={() => {
                    setPendingProvider(provider.id);
                    select.mutate(provider.id);
                  }}
                />
                <span className="font-medium">{provider.label}</span>
                <span
                  data-testid="provider-ready"
                  className={`rounded px-2 py-0.5 text-xs ${readyTone(provider)}`}
                >
                  {t(readyLabel(provider))}
                </span>
              </label>

              {secret && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <input
                    type="password"
                    data-testid="provider-key"
                    placeholder={t("settings.apiKey")}
                    value={keys[provider.id] ?? ""}
                    onChange={(event) =>
                      setKeys((previous) => ({ ...previous, [provider.id]: event.target.value }))
                    }
                    className="w-72 rounded border border-line px-2 py-1"
                  />
                  <BusyButton
                    data-testid="provider-save-key"
                    busy={saveKey.isPending && saveKey.variables?.name === secret}
                    onClick={() => saveKey.mutate({ name: secret, value: keys[provider.id] ?? "" })}
                    className="rounded bg-accent px-2 py-1 text-sm text-on-accent"
                  >
                    {t("settings.saveKey")}
                  </BusyButton>
                  {provider.console && (
                    <a
                      data-testid="provider-console"
                      href={provider.console}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm underline"
                    >
                      {t("settings.getKey")}
                    </a>
                  )}
                </div>
              )}

              {provider.needs === "cli" && (
                <div className="mt-2 grid gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {!provider.ready && (
                      <BusyButton
                        data-testid="provider-install"
                        busy={(install.isPending && install.variables === provider.id) || mine}
                        onClick={() => install.mutate(provider.id)}
                        className="rounded border border-line px-2 py-1 text-sm"
                      >
                        {t("settings.install")}
                      </BusyButton>
                    )}
                    {/* One button that flips: Sign out once signed in, Sign in otherwise. */}
                    {provider.signed_in === true && (
                      <BusyButton
                        data-testid="provider-signout"
                        busy={signout.isPending && signout.variables === provider.id}
                        onClick={() => signout.mutate(provider.id)}
                        className="rounded border border-line px-2 py-1 text-sm"
                      >
                        {t("settings.signOut")}
                      </BusyButton>
                    )}
                    {provider.signed_in !== true && (
                      <BusyButton
                        data-testid="provider-signin"
                        busy={(signin.isPending && signin.variables === provider.id) || (mine && provider.ready)}
                        disabled={!provider.ready}
                        onClick={() => signin.mutate(provider.id)}
                        className="rounded border border-line px-2 py-1 text-sm disabled:opacity-40"
                      >
                        {t("settings.signIn")}
                      </BusyButton>
                    )}
                    <span className="text-xs text-secondary" data-testid="provider-hint">
                      {!provider.ready
                        ? t(copy.missing)
                        : provider.signed_in === true
                          ? t("settings.signedInHint")
                          : t(copy.signIn)}
                    </span>
                  </div>

                  {/* The exact command, stated before the button is clicked - which is
                      what makes running it on click disclosure rather than a surprise. */}
                  {!provider.ready && (
                    <span className="text-xs text-tertiary" data-testid="provider-install-hint">
                      {provider.can_install
                        ? t("settings.installHint")
                        : t("settings.installDocsHint")}{" "}
                      {provider.install_command && <code>{provider.install_command}</code>}
                    </span>
                  )}
                  {install.isPending && install.variables === provider.id && (
                    <span className="text-xs text-secondary" data-testid="provider-starting">
                      {t("settings.starting")}
                    </span>
                  )}
                  {mine && !install.isPending && provider.signin_url && (
                    // A sign-in with no window: the link is the whole interface. The CLI has
                    // already opened it in the default browser, which may not be this one.
                    <div className="grid gap-1" data-testid="provider-signin-link">
                      <span className="text-xs text-secondary">{t("settings.signinOpened")}</span>
                      <div className="flex flex-wrap items-center gap-3 text-sm">
                        <a
                          data-testid="provider-signin-open"
                          href={provider.signin_url}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                        >
                          {t("settings.signinOpenHere")}
                        </a>
                        <button
                          type="button"
                          data-testid="provider-signin-copy"
                          className="underline"
                          onClick={() => {
                            void navigator.clipboard
                              .writeText(provider.signin_url ?? "")
                              .then(() => setCopied(provider.id));
                          }}
                        >
                          {copied === provider.id
                            ? t("settings.signinCopied")
                            : t("settings.signinCopy")}
                        </button>
                        <BusyButton
                          data-testid="provider-signin-cancel"
                          busy={cancelSignin.isPending && cancelSignin.variables === provider.id}
                          onClick={() => cancelSignin.mutate(provider.id)}
                          className="rounded border border-line px-2 py-1 text-sm"
                        >
                          {t("settings.signinCancel")}
                        </BusyButton>
                      </div>
                    </div>
                  )}
                  {mine && !install.isPending && !provider.signin_url && (
                    <span className="text-xs text-secondary" data-testid="provider-watching">
                      {t("settings.watching")}
                    </span>
                  )}
                  {/* Where the window just opened is recorded, for when it goes wrong. */}
                  {(() => {
                    const log =
                      (install.variables === provider.id && install.data?.log) ||
                      (signin.variables === provider.id && signin.data?.log);
                    // A windowless sign-in answers with its link; there was no window to show.
                    const windowless =
                      signin.variables === provider.id && signin.data?.url !== undefined;
                    return log ? (
                      <span className="text-xs text-tertiary" data-testid="provider-console-log">
                        {t(windowless ? "settings.signinLog" : "settings.consoleLog")}{" "}
                        <code className="select-all">{log}</code>
                      </span>
                    ) : null;
                  })()}
                  {failure && (
                    <span className="text-xs text-danger" data-testid="provider-error">
                      {failure instanceof Error ? failure.message : String(failure)}
                    </span>
                  )}
                  <span className="text-xs text-tertiary" data-testid="provider-plan">
                    {t(copy.plan)}
                  </span>
                  {provider.quota && (
                    <span className="text-xs text-secondary" data-testid="provider-quota">
                      {provider.quota}
                    </span>
                  )}

                  {/* Pinned to the resolved binary: a bare `claude update` would upgrade
                      whichever install the user's PATH happens to favour. */}
                  {provider.ready && provider.path && (
                    <span className="text-xs text-tertiary" data-testid="provider-path">
                      {provider.path}
                    </span>
                  )}
                  {/* Only state worth a button: the app is already saying something is
                      wrong, and an install that updates itself never lands here. */}
                  {provider.signed_in === null && provider.ready && provider.update_hint && (
                    <span
                      className="flex flex-wrap items-center gap-2 text-xs text-secondary"
                      data-testid="provider-too-old"
                    >
                      {t("settings.cliUnknownSignin")}
                      <BusyButton
                        data-testid="provider-update"
                        busy={update.isPending && update.variables === provider.id}
                        onClick={() => update.mutate(provider.id)}
                        className="rounded border border-line px-2 py-0.5 text-xs"
                      >
                        {t("settings.update")}
                      </BusyButton>
                      <code>{provider.update_hint}</code>
                    </span>
                  )}
                  {provider.account && (
                    <span className="text-xs text-secondary" data-testid="provider-account">
                      {provider.account}
                    </span>
                  )}
                </div>
              )}

              <div className="mt-2 flex items-center gap-2">
                <BusyButton
                  data-testid="provider-test"
                  busy={test.isPending && test.variables === provider.id}
                  onClick={() => test.mutate(provider.id)}
                  className="rounded border border-line px-2 py-1 text-sm"
                >
                  {t("settings.test")}
                </BusyButton>
                {result && (
                  <span
                    data-testid="provider-test-result"
                    data-ok={result.ok}
                    className={`text-sm ${result.ok ? "text-success" : "text-danger"}`}
                  >
                    {result.ok ? t("settings.testOk") : t("settings.testFailed")}
                  </span>
                )}
                {provider.detail && (
                  <span className="text-xs text-tertiary" data-testid="provider-detail">
                    {provider.detail}
                  </span>
                )}
              </div>
            </article>
          );
        })}
              </div>
            </fieldset>
          );
        })}
      </div>

      {/*
       * What happens when a plan's allowance runs out mid-week. Waiting is the default
       * and sends nothing anywhere; another provider is a deliberate choice, made here,
       * never a silent substitution — a transcript leaving the machine for a provider
       * nobody picked is exactly the surprise this app exists to avoid.
       */}
      {status.data &&
        (status.data.providers ?? []).some((provider) => provider.id === active && provider.needs === "cli") && (
          <label className="mt-4 flex flex-wrap items-center gap-2 text-sm" htmlFor="llm-fallback">
            <span className="text-secondary">{t("settings.fallbackLabel")}</span>
            <select
              id="llm-fallback"
              data-testid="provider-fallback"
              value={status.data.fallback ?? ""}
              onChange={(event) => fallback.mutate(event.target.value)}
              className="rounded-md border border-line bg-raised px-2 py-1 text-sm"
            >
              <option value="">{t("settings.fallbackNone")}</option>
              {(status.data.providers ?? [])
                .filter((provider) => provider.id !== active)
                .map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.label}
                  </option>
                ))}
            </select>
          </label>
        )}
    </section>
  );
}
