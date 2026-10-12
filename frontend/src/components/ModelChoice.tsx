import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ModelChoices, type ModelUse } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";

/** Providers whose models can be chosen (`app/llm/model_choice.py`). */
export const MODEL_PROVIDERS = new Set([
  "claude-subscription",
  "codex-subscription",
  "antigravity-subscription",
  "anthropic",
  "gemini",
  "openai",
  "ollama",
]);

/** The providers the assistant panel talks to: only they have a chat box. */
export const CHAT_PROVIDERS = new Set(["claude-subscription", "codex-subscription", "antigravity-subscription"]);

const USE_LABEL: Record<ModelUse, MessageKey> = {
  summary: "settings.models.summary",
  assistant: "settings.models.assistant",
};

/** Why a list is empty: the server sends a code, never the provider's own words. */
const LIST_ERROR: Record<string, MessageKey> = {
  "not-installed": "settings.models.err.notInstalled",
  "signed-out": "settings.models.err.signedOut",
  "no-key": "settings.models.err.noKey",
  "no-answer": "settings.models.err.noAnswer",
  refused: "settings.models.err.refused",
  "no-models": "settings.models.err.noModels",
  failed: "settings.models.err.failed",
};

/** Why a choice was not kept (`PUT /api/llm/model`'s `detail`). */
const SAVE_ERROR: Record<string, MessageKey> = {
  "not-offered": "settings.models.save.notOffered",
  "did-not-answer": "settings.models.save.didNotAnswer",
  "cloud-model": "settings.models.save.cloud",
  "not-a-model": "settings.models.save.notAModel",
};

function fill(text: string, values: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
}

/** The code inside a refused request's message (`400: {"detail":"not-offered"}`). */
function saveCode(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return Object.keys(SAVE_ERROR).find((code) => text.includes(`"${code}"`)) ?? "";
}

export const modelsKey = (provider: string) => ["llm-models", provider] as const;

/**
 * The model a provider runs, for summaries and for the conversation (D97). The list is
 * the provider's own, asked for by the server; "Automatic" is ours, and names the model
 * it stands for. A choice the provider no longer offers stays visible, marked, so going
 * back to automatic is a real change of the select: the server already runs automatic.
 *
 * `compact` is the assistant panel's: one select, no headings; a refusal shows as the
 * select's own warning and title.
 */
export default function ModelChoice({
  provider,
  uses = ["summary", "assistant"],
  compact = false,
}: {
  provider: string;
  uses?: ModelUse[];
  compact?: boolean;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: modelsKey(provider), queryFn: () => api.llmModels(provider) });
  const save = useMutation({
    mutationFn: ({ use, model }: { use: ModelUse; model: string }) => api.setLlmModel(provider, use, model),
    onSuccess: (data) => queryClient.setQueryData(modelsKey(data.provider), data),
  });
  const refresh = useMutation({
    mutationFn: () => api.llmModels(provider, true),
    onSuccess: (data) => queryClient.setQueryData(modelsKey(data.provider), data),
  });

  const data = models.data;
  if (!data) {
    if (compact) return null;
    // Asking a CLI for its list can take a few seconds the first time.
    return (
      <p data-testid="model-choice-pending" className="mt-4 text-xs text-tertiary">
        {models.isError ? t("settings.models.unavailable") : t("settings.models.loading")}
      </p>
    );
  }
  const saveError = save.error ? t(SAVE_ERROR[saveCode(save.error)] ?? "settings.models.save.failed") : "";

  const select = (use: ModelUse, current: ModelChoices) => {
    const labelOf = (id: string) => current.models.find((model) => model.id === id)?.label ?? id;
    const automatic = current.automatic[use]
      ? fill(t("settings.models.automatic"), { model: labelOf(current.automatic[use]) })
      : t("settings.models.automaticDefault");
    const chosen = current.chosen[use];
    const listed = current.models.some((model) => model.id === chosen);
    // No list to check against: the choice still runs, so it shows as it stands.
    const unlisted = !!chosen && current.models.length === 0;
    // Listed no more: the server runs automatic, but the choice is still saved.
    const dropped = !!chosen && current.models.length > 0 && !listed;
    return (
      <select
        id={`model-${use}-${provider}`}
        data-testid={`model-${use}`}
        value={chosen}
        disabled={save.isPending}
        onChange={(event) => save.mutate({ use, model: event.target.value })}
        title={compact ? t(USE_LABEL[use]) : undefined}
        aria-label={compact ? t("assistant.model") : undefined}
        aria-invalid={compact && !!saveError ? true : undefined}
        aria-describedby={compact ? `model-note-${provider} model-error-${provider}` : undefined}
        className={`max-w-full rounded-md border bg-raised px-2 py-1 ${compact ? "text-xs" : "text-sm"} ${
          compact && saveError ? "border-danger" : "border-line"
        }`}
      >
        <option value="">{automatic}</option>
        {unlisted && <option value={chosen}>{chosen}</option>}
        {dropped && (
          <option value={chosen}>{fill(t("settings.models.droppedOption"), { model: chosen })}</option>
        )}
        {current.models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </select>
    );
  };

  if (compact) {
    // Saving can take a few seconds, and a refusal must be read, not hovered. Both
    // regions are always there, so a screen reader announces a change of their text.
    return (
      <span className="flex flex-col items-end gap-0.5 text-end">
        {select(uses[0], data)}
        <span id={`model-note-${provider}`} role="status" className="text-2xs text-tertiary">
          {save.isPending ? t("settings.models.saving") : ""}
        </span>
        <span id={`model-error-${provider}`} data-testid="model-note" role="alert" className="text-2xs text-danger">
          {save.isPending ? "" : saveError}
        </span>
      </span>
    );
  }

  const listError = data.models.length === 0 && data.error ? data.error : "";
  return (
    <div data-testid="model-choice" className="mt-4 space-y-2">
      <h4 className="text-sm font-medium">{t("settings.models.title")}</h4>
      <p className="text-xs text-tertiary">
        {t("settings.models.hint")}
        {CHAT_PROVIDERS.has(provider) ? ` ${t("settings.models.hintChat")}` : ""}
      </p>
      {uses.map((use) => {
        const chosen = data.chosen[use];
        const dropped = !!chosen && data.models.length > 0 && !data.models.some((model) => model.id === chosen);
        return (
          <div key={use} className="flex flex-wrap items-center gap-2 text-sm">
            <label htmlFor={`model-${use}-${provider}`} className="w-32 text-secondary">
              {t(USE_LABEL[use])}
            </label>
            {select(use, data)}
            {dropped && (
              <span data-testid={`model-dropped-${use}`} className="text-xs text-warning">
                {fill(t("settings.models.dropped"), { model: chosen })}
              </span>
            )}
          </div>
        );
      })}
      {saveError && (
        <p data-testid="model-save-error" className="text-xs text-danger">
          {saveError}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {listError && (
          <span data-testid="model-list-error" className="text-secondary">
            {fill(t(LIST_ERROR[listError] ?? "settings.models.err.failed"), {
              status: String(data.status ?? ""),
            })}
          </span>
        )}
        <button
          type="button"
          data-testid="model-refresh"
          className="text-secondary underline"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? t("settings.models.refreshing") : t("settings.models.refresh")}
        </button>
      </div>
    </div>
  );
}
