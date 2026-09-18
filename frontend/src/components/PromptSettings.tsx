import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import BusyButton from "./BusyButton";

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

  // Arrived from a meeting's "View prompt": bring the box into view once it exists. The
  // router does not scroll to a hash, and this section renders nothing until the text loads.
  const { hash } = useLocation();
  const section = useRef<HTMLElement | null>(null);
  const loaded = Boolean(prompt.data);
  useEffect(() => {
    if (hash === "#prompt" && loaded) section.current?.scrollIntoView({ block: "start" });
  }, [hash, loaded]);

  // Never render nothing: an endpoint that 404s used to make this whole section vanish,
  // which looks exactly like a feature that was never built.
  if (prompt.isError) {
    return (
      <section data-testid="prompt-settings" className="mt-6">
        <h2 className="mb-2 text-sm font-semibold text-tertiary">{t("settings.prompt")}</h2>
        <p className="text-xs text-danger" data-testid="prompt-error">
          {prompt.error instanceof Error ? prompt.error.message : String(prompt.error)}
        </p>
      </section>
    );
  }
  if (!prompt.data) return null;
  const text = draft ?? prompt.data.text;
  const dirty = text.trim() !== prompt.data.text.trim();

  return (
    <section id="prompt" ref={section} data-testid="prompt-settings" className="mt-6 scroll-mt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-tertiary">{t("settings.prompt")}</h2>
        <span
          data-testid="prompt-state"
          className={`rounded px-2 py-0.5 text-xs ${
            prompt.data.custom
              ? "bg-warning-quiet text-warning"
              : "bg-surface-3 text-secondary"
          }`}
        >
          {prompt.data.custom ? t("settings.promptCustom") : t("settings.promptDefault")}
        </span>
      </div>
      <p className="mb-2 text-xs text-secondary">{t("settings.promptHint")}</p>
      <textarea
        data-testid="prompt-text"
        value={text}
        rows={14}
        spellCheck={false}
        onChange={(event) => setDraft(event.target.value)}
        className="w-full rounded border border-line bg-raised p-2 font-mono text-xs"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <BusyButton
          data-testid="prompt-save"
          busy={save.isPending && save.variables !== null}
          disabled={!dirty || (save.isPending && save.variables === null)}
          onClick={() => save.mutate(text.trim())}
          className="rounded bg-accent px-2 py-1 text-sm text-on-accent disabled:opacity-40"
        >
          {t("settings.promptSave")}
        </BusyButton>
        <BusyButton
          data-testid="prompt-reset"
          busy={save.isPending && save.variables === null}
          disabled={!prompt.data.custom || (save.isPending && save.variables !== null)}
          onClick={() => {
            setDraft(prompt.data.default);
            // null, not a copy of the default text: storing a copy would freeze this
            // prompt at today's wording and miss every later improvement to the shipped one.
            save.mutate(null);
          }}
          className="rounded border border-line px-2 py-1 text-sm disabled:opacity-40"
        >
          {t("settings.promptReset")}
        </BusyButton>
      </div>
    </section>
  );
}
