import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { csrfToken } from "../../api";
import { useI18n, type MessageKey } from "../../i18n";
import { Spinner } from "../BusyButton";

/**
 * The assistant (D61, D62): one conversation about the user's meetings and about
 * Upshot, from any screen. A docked column beside the content rather than a sheet over
 * it — the meeting it is asked about stays in view, and a citation opens beside it.
 *
 * The answer comes from the user's own CLI (Claude Code on their plan), which calls
 * Upshot's read-only tools; this component only shows the stream.
 */
export default function AssistantPanel({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [chatId, setChatId] = useState(() => newId());
  const [draft, setDraft] = useState("");
  const composer = useRef<HTMLTextAreaElement>(null);

  // The screen goes with every question, so "this meeting" means the one on screen.
  const context = useRef({ route: pathname, meeting_id: meetingOf(pathname) });
  context.current = { route: pathname, meeting_id: meetingOf(pathname) };

  const transport = useMemo(
    () =>
      new DefaultChatTransport<UIMessage>({
        api: "/api/assistant/chat",
        credentials: "same-origin",
        headers: () => ({ "X-CSRF-Token": csrfToken() }),
        body: () => ({ context: context.current }),
      }),
    [],
  );
  const { messages, sendMessage, status, stop, error } = useChat({ id: chatId, transport });
  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    composer.current?.focus();
  }, [chatId]);

  const send = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    void sendMessage({ text });
  };

  return (
    <aside
      data-testid="assistant-panel"
      aria-label={t("assistant.title")}
      className="flex w-[25rem] shrink-0 flex-col border-s border-line-subtle bg-surface-1"
    >
      <header className="flex items-center gap-2 border-b border-line-subtle px-3 py-2">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{t("assistant.title")}</h2>
        <button
          type="button"
          data-testid="assistant-new"
          onClick={() => setChatId(newId())}
          className="rounded-sm px-2 py-1 text-xs text-secondary hover:bg-a-200"
        >
          {t("assistant.new")}
        </button>
        <button
          type="button"
          data-testid="assistant-close"
          aria-label={t("assistant.close")}
          onClick={onClose}
          className="grid size-7 place-items-center rounded-sm text-secondary hover:bg-a-200"
        >
          <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5]">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </header>

      <div role="log" aria-label={t("assistant.title")} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3" data-testid="assistant-log">
        {messages.length === 0 && (
          <p className="text-sm text-tertiary" data-testid="assistant-empty">
            {t("assistant.empty")}
          </p>
        )}
        {messages.map((message) => (
          <Message key={message.id} message={message} />
        ))}
        {status === "submitted" && (
          <p className="flex items-center gap-2 text-sm text-tertiary" role="status">
            <Spinner /> {t("assistant.working")}
          </p>
        )}
        {error && (
          <p className="text-sm text-danger" data-testid="assistant-error" dir="auto">
            {error.message}
          </p>
        )}
      </div>

      <div className="border-t border-line-subtle p-3">
        <textarea
          ref={composer}
          data-testid="assistant-input"
          dir="auto"
          rows={2}
          value={draft}
          placeholder={t("assistant.placeholder")}
          aria-label={t("assistant.placeholder")}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Never while an input method is still composing a word.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
          className="w-full resize-none rounded-md border border-line-subtle bg-raised px-2.5 py-2 text-sm outline-none focus:border-accent"
        />
        <div className="mt-2 flex items-center justify-end gap-2">
          {busy ? (
            <button
              type="button"
              data-testid="assistant-stop"
              onClick={() => void stop()}
              className="rounded-md px-3 py-1.5 text-sm text-secondary hover:bg-a-200"
            >
              {t("assistant.stop")}
            </button>
          ) : (
            <button
              type="button"
              data-testid="assistant-send"
              onClick={send}
              disabled={!draft.trim()}
              className="rounded-md bg-accent px-3 py-1.5 text-sm text-on-accent disabled:opacity-50"
            >
              {t("assistant.send")}
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}

function Message({ message }: { message: UIMessage }) {
  if (message.role === "user") {
    const text = message.parts.map((part) => (part.type === "text" ? part.text : "")).join("");
    return (
      <div className="flex justify-end" data-testid="assistant-question">
        <p dir="auto" className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-a-200 px-3 py-2 text-sm">
          {text}
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="assistant-answer">
      {message.parts.map((part, index) => {
        if (part.type === "text") {
          return (
            <p key={index} dir="auto" className="whitespace-pre-wrap text-sm leading-relaxed">
              {part.text}
            </p>
          );
        }
        if (part.type === "dynamic-tool") {
          return <ToolStep key={index} name={part.toolName} input={part.input} state={part.state} />;
        }
        return null;
      })}
    </div>
  );
}

function ToolStep({ name, input, state }: { name: string; input: unknown; state: string }) {
  const { t } = useI18n();
  const query = typeof input === "object" && input !== null && "query" in input ? String((input as { query: unknown }).query) : "";
  const key = (`assistant.tool.${name}` as MessageKey) in catalogueKeys ? (`assistant.tool.${name}` as MessageKey) : "assistant.tool.other";
  const label = t(key).replace("{query}", query).replace("{name}", name);
  const done = state === "output-available";
  const failed = state === "output-error";
  return (
    <p className="flex items-center gap-2 text-xs text-tertiary" data-testid="assistant-tool" data-state={state}>
      {done ? "✓" : failed ? "!" : <Spinner />}
      <bdi>{label}</bdi>
    </p>
  );
}

const catalogueKeys: Record<string, true> = { "assistant.tool.search": true };

function meetingOf(pathname: string): string {
  const match = pathname.match(/^\/m\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function newId(): string {
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
