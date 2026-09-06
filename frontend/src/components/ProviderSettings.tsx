import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type LlmProvider } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";

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
  return good ? "bg-green-100 text-green-800" : "bg-neutral-200 text-neutral-700";
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
  const [watching, setWatching] = useState(false);

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
    mutationFn: api.llmSignin,
    onSuccess: (result) => {
      if (result.launched) setWatching(true);
      invalidate();
    },
  });
  const update = useMutation({ mutationFn: api.llmUpdate, onSuccess: invalidate });
  const install = useMutation({
    mutationFn: api.llmInstall,
    onSuccess: (result) => {
      // Nothing was launched only when winget is missing; then the guide is the answer.
      if (!result.launched && result.docs) window.open(result.docs, "_blank", "noreferrer");
      if (result.launched) setWatching(true);
      invalidate();
    },
  });

  // Any of the three console-launching actions can fail server-side; silence would read
  // as an unresponsive button, which is precisely how the last one was reported.
  const cliFailure = install.error ?? signin.error ?? update.error;

  // Signed in is the finish line, not installed. The Install button carries straight on
  // into the login, so stopping at "the binary exists" would stop watching in the middle
  // of the very step the user is still completing.
  const cli = (status.data?.providers ?? []).find((provider) => provider.needs === "cli");
  const signedIn = cli?.signed_in === true;
  useEffect(() => {
    if (!watching) return undefined;
    if (signedIn) {
      setWatching(false);
      return undefined;
    }
    // Give up rather than poll for the rest of the session: the login may be abandoned,
    // and an old build can never report success however long we wait.
    const timer = window.setTimeout(() => setWatching(false), 180_000);
    return () => window.clearTimeout(timer);
  }, [watching, signedIn]);

  const active = pendingProvider ?? status.data?.active;

  return (
    <section data-testid="provider-settings" className="mt-6">
      <h2 className="mb-2 text-sm font-semibold text-neutral-500">{t("settings.summaries")}</h2>
      <div className="grid gap-2">
        {(status.data?.providers ?? []).map((provider: LlmProvider) => {
          const secret = SECRET_FOR[provider.id];
          const result = tested[provider.id];
          return (
            <article
              key={provider.id}
              data-testid="provider-row"
              data-provider={provider.id}
              data-ready={provider.ready}
              data-signed-in={String(provider.signed_in)}
              data-active={provider.id === active}
              className={`rounded border p-3 ${
                provider.id === active ? "border-neutral-900" : "border-neutral-200"
              } bg-white`}
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
                    className="w-72 rounded border border-neutral-300 px-2 py-1"
                  />
                  <button
                    type="button"
                    data-testid="provider-save-key"
                    onClick={() => saveKey.mutate({ name: secret, value: keys[provider.id] ?? "" })}
                    className="rounded bg-neutral-900 px-2 py-1 text-sm text-white"
                  >
                    {t("settings.saveKey")}
                  </button>
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
                      <button
                        type="button"
                        data-testid="provider-install"
                        disabled={install.isPending}
                        onClick={() => install.mutate()}
                        className="rounded border border-neutral-300 px-2 py-1 text-sm disabled:opacity-40"
                      >
                        {t("settings.install")}
                      </button>
                    )}
                    {provider.signed_in !== true && (
                      <button
                        type="button"
                        data-testid="provider-signin"
                        disabled={!provider.ready}
                        onClick={() => signin.mutate()}
                        className="rounded border border-neutral-300 px-2 py-1 text-sm disabled:opacity-40"
                      >
                        {t("settings.signIn")}
                      </button>
                    )}
                    <span className="text-xs text-neutral-600" data-testid="provider-hint">
                      {!provider.ready
                        ? t("settings.cliMissing")
                        : provider.signed_in === true
                          ? t("settings.signedInHint")
                          : t("settings.signInHint")}
                    </span>
                  </div>

                  {/* The exact command, stated before the button is clicked - which is
                      what makes running it on click disclosure rather than a surprise. */}
                  {!provider.ready && (
                    <span className="text-xs text-neutral-500" data-testid="provider-install-hint">
                      {provider.can_install
                        ? t("settings.installHint")
                        : t("settings.installDocsHint")}{" "}
                      {provider.install_command && <code>{provider.install_command}</code>}
                    </span>
                  )}
                  {install.isPending && (
                    <span className="text-xs text-neutral-600" data-testid="provider-starting">
                      {t("settings.starting")}
                    </span>
                  )}
                  {watching && !install.isPending && (
                    <span className="text-xs text-neutral-600" data-testid="provider-watching">
                      {t("settings.watching")}
                    </span>
                  )}
                  {cliFailure && (
                    <span className="text-xs text-red-700" data-testid="provider-error">
                      {cliFailure instanceof Error ? cliFailure.message : String(cliFailure)}
                    </span>
                  )}
                  <span className="text-xs text-neutral-500">{t("settings.cliPlanNote")}</span>

                  {/* Pinned to the resolved binary: a bare `claude update` would upgrade
                      whichever install the user's PATH happens to favour. */}
                  {provider.ready && provider.path && (
                    <span className="text-xs text-neutral-500" data-testid="provider-path">
                      {provider.path}
                    </span>
                  )}
                  {/* Only state worth a button: the app is already saying something is
                      wrong, and an install that updates itself never lands here. */}
                  {provider.signed_in === null && provider.ready && provider.update_hint && (
                    <span
                      className="flex flex-wrap items-center gap-2 text-xs text-neutral-600"
                      data-testid="provider-too-old"
                    >
                      {t("settings.cliUnknownSignin")}
                      <button
                        type="button"
                        data-testid="provider-update"
                        onClick={() => update.mutate()}
                        className="rounded border border-neutral-300 px-2 py-0.5 text-xs"
                      >
                        {t("settings.update")}
                      </button>
                      <code>{provider.update_hint}</code>
                    </span>
                  )}
                  {provider.account && (
                    <span className="text-xs text-neutral-600" data-testid="provider-account">
                      {provider.account}
                    </span>
                  )}
                </div>
              )}

              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  data-testid="provider-test"
                  onClick={() => test.mutate(provider.id)}
                  className="rounded border border-neutral-300 px-2 py-1 text-sm"
                >
                  {t("settings.test")}
                </button>
                {result && (
                  <span
                    data-testid="provider-test-result"
                    data-ok={result.ok}
                    className={`text-sm ${result.ok ? "text-green-700" : "text-red-700"}`}
                  >
                    {result.ok ? t("settings.testOk") : t("settings.testFailed")}
                  </span>
                )}
                {provider.detail && (
                  <span className="text-xs text-neutral-500" data-testid="provider-detail">
                    {provider.detail}
                  </span>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
