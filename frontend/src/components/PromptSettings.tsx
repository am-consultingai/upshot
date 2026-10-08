import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import Button from "./Button";
import { Loading, Skeleton } from "./Skeleton";
import { hunks, lineDiff } from "../lib/lineDiff";

/** The instructions sent with every summary, shown in full and editable.
 *
 * The box holds the whole prompt rather than an "extra instructions" field appended to a
 * hidden one: a prompt you cannot see is a prompt you cannot debug, and the first
 * question about a disappointing summary is always what was actually asked for.
 */
export default function PromptSettings() {
  const { t, locale } = useI18n();
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

  /*
   * No self-scrolling any more. "View prompt" used to land here and then get
   * pushed off it: the provider section above grows as its status query resolves
   * and its key rows appear, so the thing you had just scrolled to slid down the
   * page. The prompt is its own settings section now, with nothing above it that
   * can change height.
   */
  // Never render nothing: an endpoint that 404s used to make this whole section vanish,
  // which looks exactly like a feature that was never built.
  if (prompt.isError) {
    return (
      <section data-testid="prompt-settings">
        <h2 className="mb-2 text-sm font-semibold text-tertiary">{t("settings.prompt")}</h2>
        <p className="text-xs text-danger" data-testid="prompt-error">
          {prompt.error instanceof Error ? prompt.error.message : String(prompt.error)}
        </p>
      </section>
    );
  }
  if (!prompt.data)
    return (
      // The heading, its state chip and the editor box, at the editor's height.
      <Loading testid="prompt-loading">
        <div className="mb-2 flex items-center gap-2">
          <Skeleton className="h-3.5 w-28" />
          <Skeleton className="h-4 w-16 rounded-full" />
        </div>
        <Skeleton className="h-64 w-full rounded-md" />
      </Loading>
    );
  const text = draft ?? prompt.data.text;
  const dirty = text.trim() !== prompt.data.text.trim();
  // How this text differs from the shipped prompt, saved or not: the question an
  // edited prompt raises is "what did I change?", and the badge alone cannot say.
  const changed =
    text.trim() === prompt.data.default.trim() ? null : hunks(lineDiff(prompt.data.default.trim(), text.trim()));

  return (
    <section id="prompt" data-testid="prompt-settings">
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
      {/*
       * Prose, not code. The prompt is instructions in sentences, and a monospace wall
       * at 12px made it read like a config file nobody was meant to touch. The UI face
       * at reading size, room to breathe, and the box grows with its text.
       */}
      <textarea
        data-testid="prompt-text"
        value={text}
        rows={16}
        spellCheck={false}
        onChange={(event) => setDraft(event.target.value)}
        className="block max-h-[70vh] min-h-[16rem] w-full resize-y rounded-lg bg-raised px-4 py-3 text-sm leading-relaxed shadow-[var(--shadow-ring)] [field-sizing:content] focus:shadow-[0_0_0_1px_var(--accent)]"
      />
      <p data-testid="prompt-count" className="mt-1 text-end text-2xs text-tertiary tabular-nums">
        {t("settings.promptChars").replace("{n}", text.length.toLocaleString(locale))}
      </p>
      {changed && (
        <section data-testid="prompt-diff" className="mt-3">
          <h3 className="mb-1.5 text-xs font-medium text-secondary">{t("settings.promptDiff")}</h3>
          <div dir="auto" className="overflow-x-auto rounded-lg bg-surface-1 py-1.5 font-mono text-2xs leading-relaxed shadow-[var(--shadow-ring-subtle)]">
            {changed.map((line, index) =>
              line === null ? (
                <div key={index} aria-hidden="true" className="px-3 text-tertiary">
                  ⋯
                </div>
              ) : (
                <div
                  key={index}
                  data-testid="prompt-diff-line"
                  data-kind={line.kind}
                  className={`flex gap-2 px-3 whitespace-pre-wrap ${
                    line.kind === "add"
                      ? "bg-success-quiet text-primary"
                      : line.kind === "remove"
                        ? "bg-danger-quiet text-secondary line-through decoration-from-font"
                        : "text-tertiary"
                  }`}
                >
                  <span aria-hidden="true" className="w-2 shrink-0 select-none">
                    {line.kind === "add" ? "+" : line.kind === "remove" ? "−" : ""}
                  </span>
                  {/* Said, not only coloured: a screen reader hears which lines are which. */}
                  <span className="sr-only">
                    {line.kind === "add" ? t("settings.promptAdded") : line.kind === "remove" ? t("settings.promptRemoved") : ""}
                  </span>
                  <span className="min-w-0">{line.text || " "}</span>
                </div>
              ),
            )}
          </div>
        </section>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          data-testid="prompt-save"
          busy={save.isPending && save.variables !== null}
          disabled={!dirty || (save.isPending && save.variables === null)}
          onClick={() => save.mutate(text.trim())}
          variant="primary"
          size="md"
        >
          {t("settings.promptSave")}
        </Button>
        <Button
          data-testid="prompt-reset"
          busy={save.isPending && save.variables === null}
          disabled={!prompt.data.custom || (save.isPending && save.variables !== null)}
          onClick={() => {
            setDraft(prompt.data.default);
            // null, not a copy of the default text: storing a copy would freeze this
            // prompt at today's wording and miss every later improvement to the shipped one.
            save.mutate(null);
          }}
          size="md"
        >
          {t("settings.promptReset")}
        </Button>
      </div>
    </section>
  );
}
