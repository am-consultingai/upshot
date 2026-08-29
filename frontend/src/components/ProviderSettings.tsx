import { useState } from "react";
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
function readyLabel(needs: string, ready: boolean): MessageKey {
  if (needs === "cli") return ready ? "settings.cliInstalled" : "settings.cliNotInstalled";
  if (needs === "key") return ready ? "settings.keySet" : "settings.keyMissing";
  return "settings.localReady";
}

export default function ProviderSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [keys, setKeys] = useState<Record<string, string>>({});
  // The radio must respond to the click immediately, not after the server round-trip —
  // otherwise it visibly snaps back and the UI reads as broken.
  const [pendingProvider, setPendingProvider] = useState<string | null>(null);
  const [tested, setTested] = useState<Record<string, { ok: boolean; detail: string }>>({});

  const status = useQuery({ queryKey: ["llm-status"], queryFn: api.llmStatus });
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
  const signin = useMutation({ mutationFn: api.llmSignin, onSuccess: invalidate });

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
                  className={`rounded px-2 py-0.5 text-xs ${
                    provider.ready
                      ? "bg-green-100 text-green-800"
                      : "bg-neutral-200 text-neutral-700"
                  }`}
                >
                  {t(readyLabel(provider.needs, provider.ready))}
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
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    data-testid="provider-signin"
                    disabled={!provider.ready}
                    onClick={() => signin.mutate()}
                    className="rounded border border-neutral-300 px-2 py-1 text-sm disabled:opacity-40"
                  >
                    {t("settings.signIn")}
                  </button>
                  <span className="text-xs text-neutral-600" data-testid="provider-hint">
                    {provider.ready ? t("settings.signInHint") : t("settings.cliMissing")}
                  </span>
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
