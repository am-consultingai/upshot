import { useContext, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type LlmProvider } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import BusyButton from "./BusyButton";
import { PinnedContext } from "./SettingRow";
import Tooltip from "./Tooltip";
import { openExternal } from "../lib/external";
import { VendorLogo, type VendorId } from "../setup/logos";
import { Loading, SkeletonRows } from "./Skeleton";

/** The vendor's mark beside its row, as setup shows it (product owner, 2026-09-28). */
const LOGO: Record<string, VendorId | undefined> = {
  "claude-subscription": "claude",
  anthropic: "claude",
  "codex-subscription": "codex",
  openai: "codex",
  "antigravity-subscription": "antigravity",
  gemini: "gemini",
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
  return provider.ready ? "settings.keySet" : "settings.keyMissing";
}

/** Green only when the provider would actually work right now. */
function readyTone(provider: LlmProvider): string {
  const good = provider.needs === "cli" ? provider.ready && provider.signed_in !== false : provider.ready;
  return good ? "bg-success-quiet text-success" : "bg-surface-3 text-secondary";
}

/**
 * How the providers are grouped on screen, in the order they appear: a subscription you
 * already pay for first, a key you have to create and fund second. "Transcripts only" and
 * the local model are not offered here for now (product owner, 2026-09-28).
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
] as const satisfies readonly {
  id: string;
  label: MessageKey;
  hint: MessageKey;
  holds: (provider: LlmProvider) => boolean;
}[];

/** Not rows here: summaries off is simply no row chosen, and the local model is set aside. */
const NOT_LISTED = new Set(["none", "ollama"]);

/** A sign-in that ends on a code pasted back (Claude's may, Antigravity's does, D75, D79). */
const TAKES_CODE = new Set(["claude-subscription", "antigravity-subscription"]);

const BUTTON = "rounded border border-line px-2 py-1 text-sm disabled:opacity-40";

/**
 * Whether the wait on an install or sign-in is over: the CLI reports signed in, or a
 * status fetched after the launch says the process is gone. An older server that does not
 * report it leaves only the timeout to end it.
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

/**
 * Settings → AI: one line per provider — its mark, its name, whether it is ready, the one
 * action it needs, and Test (product owner, 2026-09-28: no explanations under each). A
 * second line appears only while something waits on the user (a sign-in's link and code)
 * or went wrong.
 *
 * Install and sign-in run with no window, as setup's do (D75): the vendor's tool opens its
 * page in the default browser, and a code, where there is one, is pasted in the row.
 * Antigravity's sign-out is the exception: it only works typed into agy (D79).
 */
export default function ProviderSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [codes, setCodes] = useState<Record<string, string>>({});
  // The radio must respond to the click immediately, not after the server round-trip —
  // otherwise it visibly snaps back and the UI reads as broken.
  const [pendingProvider, setPendingProvider] = useState<string | null>(null);
  const [tested, setTested] = useState<Record<string, { ok: boolean; detail: string }>>({});
  // Which CLI's install or sign-in is being waited on, or null, and since when: a status
  // fetched before the launch says nothing about it.
  const [watching, setWatching] = useState<string | null>(null);
  const since = useRef(0);
  const watch = (provider: string) => {
    since.current = Date.now();
    setWatching(provider);
  };

  // A sign-out in a window (Antigravity): watched until the row reads signed out or the
  // window closes, the other way round from a sign-in.
  const [signingOut, setSigningOut] = useState<string | null>(null);
  const signedOutSince = useRef(0);

  const status = useQuery({
    queryKey: ["llm-status"],
    queryFn: api.llmStatus,
    refetchInterval: watching || signingOut ? 3000 : false,
  });
  useEffect(() => {
    if (!signingOut) return undefined;
    const row = status.data?.providers.find((provider) => provider.id === signingOut);
    const closed = row?.console_open === false && status.dataUpdatedAt > signedOutSince.current;
    if (row?.signed_in !== true || closed) {
      setSigningOut(null);
      return undefined;
    }
    const timer = window.setTimeout(() => setSigningOut(null), 300_000);
    return () => window.clearTimeout(timer);
  }, [signingOut, status.data, status.dataUpdatedAt]);
  const invalidate = () => queryClient.invalidateQueries();

  const select = useMutation({
    mutationFn: (provider: string) => api.putSettings({ "llm.provider": provider }),
    onSuccess: invalidate,
    onSettled: () => setPendingProvider(null),
  });
  const saveKey = useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) => api.putSecrets({ [name]: value }),
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
    // `background`: no window, as setup does it (D75).
    mutationFn: (provider: string) => api.llmSignin(provider, true),
    onSuccess: (result, provider) => {
      if (result.launched) watch(provider);
      invalidate();
    },
  });
  const sendCode = useMutation({
    mutationFn: ({ provider, code }: { provider: string; code: string }) => api.llmSigninCode(provider, code),
    onSuccess: invalidate,
  });
  const signout = useMutation({
    mutationFn: (provider: string) => api.llmSignout(provider),
    onSuccess: (result, provider) => {
      if (result.launched) {
        signedOutSince.current = Date.now();
        setSigningOut(provider);
      }
      invalidate();
    },
  });
  const cancelSignin = useMutation({
    mutationFn: (provider: string) => api.llmSigninCancel(provider),
    onSuccess: () => {
      setWatching(null);
      invalidate();
    },
  });
  const install = useMutation({
    // `background`: installed with no window; Sign in is the next press (D75).
    mutationFn: (provider: string) => api.llmInstall(provider, true),
    onSuccess: (result, provider) => {
      // Nothing was launched only when no installer can run; then the guide is the answer.
      if (!result.launched && result.docs) void openExternal(result.docs);
      if (result.launched) watch(provider);
      invalidate();
    },
  });
  const fallback = useMutation({
    mutationFn: (provider: string) => api.putSettings({ "llm.fallback_provider": provider }),
    onSuccess: invalidate,
  });

  // Signed in is the finish line of a sign-in; the process ending is the finish line of
  // an install (which no longer signs in by itself) and of an abandoned sign-in.
  const cli = (status.data?.providers ?? []).find((provider) => provider.id === watching);
  const finished = watchFinished({
    signedIn: cli?.signed_in === true,
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
    // Give up rather than poll for the rest of the session: the login may be abandoned.
    const timer = window.setTimeout(() => setWatching(null), 600_000);
    return () => window.clearTimeout(timer);
  }, [watching, finished]);

  const active = pendingProvider ?? status.data?.active;
  const rows = (status.data?.providers ?? []).filter((provider) => !NOT_LISTED.has(provider.id));
  /*
   * `llm.provider` can hold a value that is not one of the rows — "fake", which the
   * demo launcher pins, or a provider that has since been removed. Every radio then
   * renders unchecked and the screen silently claims nothing is selected, which is
   * the one thing it must not do: summaries *are* being produced by something.
   * "none" is the exception: no row chosen is exactly what it means.
   */
  const pinnedBy = useContext(PinnedContext)["llm.provider"];
  const unlisted =
    active && active !== "none" && !rows.some((provider) => provider.id === active) ? active : null;

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
      {status.isLoading && (
        // A group's worth of provider rows: a radio, a name, a line of explanation.
        <Loading>
          <SkeletonRows rows={3} lead="check" rowClassName="py-2.5" />
        </Loading>
      )}
      <div className="grid gap-4">
        {GROUPS.map((group) => {
          const members = rows.filter(group.holds);
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
                <legend data-testid="provider-group-label" className="cursor-help px-1 text-xs font-medium text-secondary">
                  {t(group.label)}
                </legend>
              </Tooltip>
              <div className="grid gap-1.5">
                {members.map((provider: LlmProvider) => {
                  const secret = SECRET_FOR[provider.id];
                  const result = tested[provider.id];
                  const mine = watching === provider.id;
                  const signingIn = mine && !!provider.signin_url;
                  const failure =
                    (install.variables === provider.id && install.error) ||
                    (signin.variables === provider.id && signin.error) ||
                    (signout.variables === provider.id && signout.error) ||
                    (sendCode.variables?.provider === provider.id && sendCode.error) ||
                    null;
                  return (
                    <article
                      key={provider.id}
                      data-testid="provider-row"
                      data-provider={provider.id}
                      data-ready={provider.ready}
                      data-signed-in={String(provider.signed_in)}
                      data-active={provider.id === active}
                      className={`rounded border px-3 py-2 ${
                        provider.id === active ? "border-accent" : "border-line-subtle"
                      } bg-raised`}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <label className="flex min-w-0 items-center gap-2">
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
                          {LOGO[provider.id] && <VendorLogo vendor={LOGO[provider.id]!} size={18} />}
                          <span className="font-medium">{provider.label}</span>
                        </label>
                        <span data-testid="provider-ready" className={`rounded px-2 py-0.5 text-xs ${readyTone(provider)}`}>
                          {t(readyLabel(provider))}
                        </span>

                        <span className="ms-auto flex flex-wrap items-center gap-2">
                          {secret && (
                            <>
                              <input
                                type="password"
                                data-testid="provider-key"
                                placeholder={t("settings.apiKey")}
                                value={keys[provider.id] ?? ""}
                                onChange={(event) =>
                                  setKeys((previous) => ({ ...previous, [provider.id]: event.target.value }))
                                }
                                className="w-48 rounded border border-line px-2 py-1 text-sm"
                              />
                              <BusyButton
                                data-testid="provider-save-key"
                                busy={saveKey.isPending && saveKey.variables?.name === secret}
                                disabled={!(keys[provider.id] ?? "").trim()}
                                onClick={() => saveKey.mutate({ name: secret, value: keys[provider.id] ?? "" })}
                                className={BUTTON}
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
                            </>
                          )}

                          {provider.needs === "cli" && !provider.ready && (
                            <BusyButton
                              data-testid="provider-install"
                              busy={(install.isPending && install.variables === provider.id) || mine}
                              onClick={() => install.mutate(provider.id)}
                              className={BUTTON}
                            >
                              {t("settings.install")}
                            </BusyButton>
                          )}
                          {provider.needs === "cli" && provider.ready && provider.signed_in === true && (
                            <BusyButton
                              data-testid="provider-signout"
                              busy={(signout.isPending && signout.variables === provider.id) || signingOut === provider.id}
                              disabled={provider.can_sign_out === false}
                              onClick={() => signout.mutate(provider.id)}
                              className={BUTTON}
                            >
                              {t("settings.signOut")}
                            </BusyButton>
                          )}
                          {provider.needs === "cli" && provider.ready && provider.signed_in !== true && (
                            <BusyButton
                              data-testid="provider-signin"
                              busy={(signin.isPending && signin.variables === provider.id) || (mine && !signingIn)}
                              onClick={() => (signingIn ? undefined : signin.mutate(provider.id))}
                              className={BUTTON}
                            >
                              {t("settings.signIn")}
                            </BusyButton>
                          )}

                          <BusyButton
                            data-testid="provider-test"
                            busy={test.isPending && test.variables === provider.id}
                            onClick={() => test.mutate(provider.id)}
                            className={BUTTON}
                          >
                            {t("settings.test")}
                          </BusyButton>
                          {result && (
                            <span
                              data-testid="provider-test-result"
                              data-ok={result.ok}
                              title={result.detail}
                              className={`text-sm ${result.ok ? "text-success" : "text-danger"}`}
                            >
                              {result.ok ? t("settings.testOk") : t("settings.testFailed")}
                            </span>
                          )}
                        </span>
                      </div>

                      {/* Only while a sign-in waits on the user: its page, and the code box. */}
                      {signingIn && (
                        <form
                          data-testid="provider-signin-link"
                          className="mt-2 flex flex-wrap items-center gap-2 text-sm"
                          onSubmit={(event) => {
                            event.preventDefault();
                            const code = (codes[provider.id] ?? "").trim();
                            if (code) sendCode.mutate({ provider: provider.id, code });
                          }}
                        >
                          {TAKES_CODE.has(provider.id) && (
                            <>
                              <input
                                data-testid="provider-signin-code"
                                dir="ltr"
                                placeholder={t("settings.signinCodePlaceholder")}
                                value={codes[provider.id] ?? ""}
                                onChange={(event) =>
                                  setCodes((previous) => ({ ...previous, [provider.id]: event.target.value }))
                                }
                                autoComplete="off"
                                spellCheck={false}
                                className="w-64 rounded border border-line px-2 py-1 font-mono text-sm"
                              />
                              <BusyButton
                                type="submit"
                                data-testid="provider-signin-submit"
                                busy={sendCode.isPending && sendCode.variables?.provider === provider.id}
                                disabled={!(codes[provider.id] ?? "").trim()}
                                className={BUTTON}
                              >
                                {t("settings.signinSubmitCode")}
                              </BusyButton>
                            </>
                          )}
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
                            data-testid="provider-signin-cancel"
                            onClick={() => cancelSignin.mutate(provider.id)}
                            className="underline"
                          >
                            {t("settings.signinCancel")}
                          </button>
                        </form>
                      )}
                      {signingOut === provider.id && (
                        <p data-testid="provider-signout-window" className="mt-2 text-xs text-secondary">
                          {t("settings.signOutInWindow")}
                        </p>
                      )}
                      {failure && (
                        <p data-testid="provider-error" className="mt-2 text-xs text-danger">
                          {failure instanceof Error ? failure.message : String(failure)}
                        </p>
                      )}
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
      {status.data && rows.some((provider) => provider.id === active && provider.needs === "cli") && (
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
            {rows
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
