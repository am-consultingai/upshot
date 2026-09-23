import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { api, type AskAnswer } from "../api";
import { useI18n } from "../i18n";
import { stamp } from "../lib/speakers";
import { Spinner } from "./BusyButton";
import Menu from "./Menu";
import Tooltip from "./Tooltip";

interface Turn {
  question: string;
  answer?: AskAnswer;
  error?: string;
}

/**
 * Ask this meeting a question, in words, and get an answer with its sources.
 *
 * The answer comes from the same model that wrote the summary, reading this meeting's
 * transcript — or, with the scope switched, this meeting and the ones related to it.
 * Every answer carries the moments it relied on as timestamps that play the
 * recording from there, so a claim can be checked in the words it came from: the
 * same loop the transcript closes for the summary.
 *
 * The scope chip is a real choice with a menu, not a label: "this meeting" is
 * cheaper and answers most questions; "related meetings" is what "when did we first
 * talk about this" needs.
 */
export default function AskMeeting({
  meetingId,
  onSeek,
}: {
  meetingId: string;
  onSeek: (seconds: number) => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<"meeting" | "related">("meeting");
  const [turns, setTurns] = useState<Turn[]>([]);

  const ask = useMutation({
    mutationFn: (text: string) => api.ask(meetingId, text, scope),
    onSuccess: (answer, text) =>
      setTurns((all) => all.map((turn) => (turn.question === text && !turn.answer ? { ...turn, answer } : turn))),
    onError: (error, text) =>
      setTurns((all) =>
        all.map((turn) =>
          turn.question === text && !turn.answer
            ? { ...turn, error: String(error instanceof Error ? error.message : error).replace(/^\d+: /, "") }
            : turn,
        ),
      ),
  });

  const submit = () => {
    const text = question.trim();
    if (!text || ask.isPending) return;
    setTurns((all) => [...all, { question: text }]);
    setQuestion("");
    ask.mutate(text);
  };

  return (
    <section data-testid="ask-meeting">
      <Tooltip label={t("ask.heading")} hint={t("help.ask")}>
        <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("ask.heading")}</h2>
      </Tooltip>

      {turns.map((turn, index) => (
        <div key={index} data-testid="ask-turn" className="mt-3 text-sm">
          <p className="font-medium text-primary">{turn.question}</p>
          {!turn.answer && !turn.error && (
            <p className="mt-1 flex items-center gap-1.5 text-tertiary" role="status">
              <Spinner className="text-accent" /> {t("ask.thinking")}
            </p>
          )}
          {turn.error && (
            <p data-testid="ask-error" className="mt-1 text-danger">
              {t("ask.failed")} {turn.error}
            </p>
          )}
          {turn.answer && (
            <div className="mt-1">
              <p data-testid="ask-answer" className="whitespace-pre-wrap leading-relaxed text-secondary">
                {turn.answer.answer}
              </p>
              {turn.answer.citations.length > 0 && (
                <p className="mt-1 flex flex-wrap gap-1.5">
                  {turn.answer.citations.map((citation, at) => (
                    <button
                      key={at}
                      type="button"
                      data-testid="ask-citation"
                      data-at-ms={citation.at_ms}
                      title={citation.text}
                      onClick={() =>
                        citation.meeting_id && citation.meeting_id !== meetingId
                          ? navigate(`/m/${citation.meeting_id}?at=${citation.at_ms}`)
                          : onSeek(citation.at_ms / 1000)
                      }
                      className="font-mono text-2xs text-accent underline decoration-from-font underline-offset-[3px] hover:brightness-110"
                    >
                      ({stamp(citation.at_ms / 1000)})
                    </button>
                  ))}
                </p>
              )}
            </div>
          )}
        </div>
      ))}

      <div className="mt-3 rounded-lg bg-surface-1 p-2.5 shadow-[var(--shadow-ring-subtle)] focus-within:shadow-[0_0_0_1px_var(--accent)]">
        <textarea
          data-testid="ask-input"
          rows={2}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={t("ask.placeholder")}
          aria-label={t("ask.heading")}
          className="no-focus-ring block w-full resize-none bg-transparent px-0.5 text-sm outline-none placeholder:text-tertiary"
        />
        <div className="mt-1.5 flex items-center gap-2">
          <ScopeChip scope={scope} onChange={setScope} />
          <button
            type="button"
            data-testid="ask-send"
            aria-label={t("ask.send")}
            title={t("ask.send")}
            disabled={!question.trim() || ask.isPending}
            onClick={submit}
            className="ms-auto grid size-6.5 place-items-center rounded-full bg-accent text-on-accent hover:brightness-110 active:translate-y-px disabled:opacity-40"
          >
            <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.8]" strokeLinecap="round">
              <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" />
            </svg>
          </button>
        </div>
      </div>
    </section>
  );
}

function ScopeChip({
  scope,
  onChange,
}: {
  scope: "meeting" | "related";
  onChange: (next: "meeting" | "related") => void;
}) {
  const { t } = useI18n();
  return (
    <Tooltip label={t("ask.scope")} hint={t("help.askScope")}>
    <span data-testid="ask-scope" data-scope={scope}>
      <Menu
        label={t("ask.scope")}
        testid="ask-scope-trigger"
        triggerClassName="inline-flex h-5.5 items-center gap-1 rounded-full px-2 text-2xs text-secondary shadow-[var(--shadow-ring)] hover:bg-a-200 hover:text-primary"
        trigger={
          <>
            {scope === "meeting" ? t("ask.scopeMeeting") : t("ask.scopeRelated")}
            <svg viewBox="0 0 16 16" className="size-2.5 fill-none stroke-current stroke-[1.8]">
              <path d="M4 6.5 8 10.5l4-4" />
            </svg>
          </>
        }
        items={[
          { id: "meeting", label: t("ask.scopeMeeting"), run: () => onChange("meeting") },
          { id: "related", label: t("ask.scopeRelated"), run: () => onChange("related") },
        ]}
      />
    </span>
    </Tooltip>
  );
}
