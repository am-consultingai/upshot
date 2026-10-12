import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ModelUse } from "../api";
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

const USE_LABEL: Record<ModelUse, MessageKey> = {
  summary: "settings.models.summary",
  assistant: "settings.models.assistant",
};

function fill(text: string, values: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
}

export const modelsKey = (provider: string) => ["llm-models", provider] as const;

/**
 * The model a provider runs, for summaries and for the conversation (2026-10-12). The
 * list is the provider's own, asked for by the server; "Automatic" is ours, and names the
 * model it stands for. A choice the provider no longer offers says so: the server has
 * already gone back to automatic for it.
 *
 * `compact` is the assistant panel's: one select, no headings.
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
    onSuccess: (data) => queryClient.setQueryData(modelsKey(provider), data),
  });
  const refresh = useMutation({
    mutationFn: () => api.llmModels(provider, true),
    onSuccess: (data) => queryClient.setQueryData(modelsKey(provider), data),
  });

  const data = models.data;
  if (!data) return null;
  const labelOf = (id: string) => data.models.find((model) => model.id === id)?.label ?? id;
  const automaticLabel = (use: ModelUse) =>
    data.automatic[use]
      ? fill(t("settings.models.automatic"), { model: labelOf(data.automatic[use]) })
      : t("settings.models.automaticDefault");

  const select = (use: ModelUse) => {
    const chosen = data.chosen[use];
    const offered = !chosen || data.models.length === 0 || data.models.some((model) => model.id === chosen);
    return (
      <select
        id={`model-${use}-${provider}`}
        data-testid={`model-${use}`}
        value={offered ? chosen : ""}
        disabled={save.isPending}
        onChange={(event) => save.mutate({ use, model: event.target.value })}
        title={compact ? t(USE_LABEL[use]) : undefined}
        aria-label={compact ? t("assistant.model") : undefined}
        className={`max-w-full rounded-md border border-line bg-raised px-2 py-1 ${compact ? "text-xs" : "text-sm"}`}
      >
        <option value="">{automaticLabel(use)}</option>
        {data.models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </select>
    );
  };

  if (compact) return select(uses[0]);

  return (
    <div data-testid="model-choice" className="mt-4 space-y-2">
      <h4 className="text-sm font-medium">{t("settings.models.title")}</h4>
      <p className="text-xs text-tertiary">{t("settings.models.hint")}</p>
      {uses.map((use) => {
        const chosen = data.chosen[use];
        const dropped = !!chosen && data.models.length > 0 && !data.models.some((model) => model.id === chosen);
        return (
          <div key={use} className="flex flex-wrap items-center gap-2 text-sm">
            <label htmlFor={`model-${use}-${provider}`} className="w-32 text-secondary">
              {t(USE_LABEL[use])}
            </label>
            {select(use)}
            {dropped && (
              <span data-testid={`model-dropped-${use}`} className="text-xs text-warning">
                {fill(t("settings.models.dropped"), { model: chosen })}
              </span>
            )}
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {data.models.length === 0 && data.error && (
          <span data-testid="model-list-error" className="text-secondary">
            {fill(t("settings.models.none"), { reason: data.error })}
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
