import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

/** The instructions sent with every summary, shown in full and editable.
 *
 * The box holds the whole prompt rather than an "extra instructions" field appended to a
 * hidden one: a prompt you cannot see is a prompt you cannot debug, and the first
 * question about a disappointing summary is always what was actually asked for.
 */
export default function PromptSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const prompt = useQuery({ queryKey: ["llm-prompt"], queryFn: api.llmPrompt });
  const [draft, setDraft] = useState<string | null>(null);

  // Adopt the server's text when it arrives, and again after a save or reset. Binding
  // the textarea straight to the query would instead overwrite the user mid-sentence.
  useEffect(() => {
    if (prompt.data) setDraft(prompt.data.text);
  }, [prompt.data]);

  const save = useMutation({
    mutationFn: (value: string | null) => api.putSettings({ "llm.summary_prompt": value }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  // Never render nothing: an endpoint that 404s used to make this whole section vanish,
  // which looks exactly like a feature that was never built.
  if (prompt.isError) {
    return (
      <section data-testid="prompt-settings" className="mt-6">
        <h2 className="mb-2 text-sm font-semibold text-neutral-500">{t("settings.prompt")}</h2>
        <p className="text-xs text-red-700" data-testid="prompt-error">
          {prompt.error instanceof Error ? prompt.error.message : String(prompt.error)}
        </p>
      </section>
    );
  }
  if (!prompt.data) return null;
  const text = draft ?? prompt.data.text;
  const dirty = text.trim() !== prompt.data.text.trim();

  return (
    <section data-testid="prompt-settings" className="mt-6">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-neutral-500">{t("settings.prompt")}</h2>
        <span
          data-testid="prompt-state"
          className={`rounded px-2 py-0.5 text-xs ${
            prompt.data.custom
              ? "bg-amber-100 text-amber-800"
              : "bg-neutral-200 text-neutral-700"
          }`}
        >
          {prompt.data.custom ? t("settings.promptCustom") : t("settings.promptDefault")}
        </span>
      </div>
      <p className="mb-2 text-xs text-neutral-600">{t("settings.promptHint")}</p>
      <textarea
        data-testid="prompt-text"
        value={text}
        rows={14}
        spellCheck={false}
        onChange={(event) => setDraft(event.target.value)}
        className="w-full rounded border border-neutral-300 bg-white p-2 font-mono text-xs"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="prompt-save"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(text.trim())}
          className="rounded bg-neutral-900 px-2 py-1 text-sm text-white disabled:opacity-40"
        >
          {t("settings.promptSave")}
        </button>
        <button
          type="button"
          data-testid="prompt-reset"
          disabled={!prompt.data.custom || save.isPending}
          onClick={() => {
            setDraft(prompt.data.default);
            // null, not a copy of the default text: storing a copy would freeze this
            // prompt at today's wording and miss every later improvement to the shipped one.
            save.mutate(null);
          }}
          className="rounded border border-neutral-300 px-2 py-1 text-sm disabled:opacity-40"
        >
          {t("settings.promptReset")}
        </button>
      </div>
    </section>
  );
}
