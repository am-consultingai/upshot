import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { api, csrfToken } from "../../api";
import { useI18n, type MessageKey } from "../../i18n";
import { Spinner } from "../BusyButton";
import { confirmDialog } from "../ConfirmDialog";
import type { Citation } from "./Answer";

// The Markdown renderer is the heaviest thing in the panel; it loads with the first answer.
const Answer = lazy(() => import("./Answer"));

/**
 * The assistant (D61, D62): one conversation about the user's meetings and about
 * Upshot, from any screen. It floats above the content from the round button in the
 * corner (AssistantLauncher), the way a chat does, rather than taking a column of the
 * window (changed from D62's docked column at the product owner's request, 2026-09-26).
 *
 * The answer comes from the user's own CLI (Claude Code on their plan), which calls
 * Upshot's read-only tools; this component shows the stream, keeps the conversation,
 * and says in words what is happening and who is answering.
 */

const CURRENT = "upshot.assistant.chat";
const DISCLOSED = "upshot.assistant.disclosed";

function read(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* a private window: the database still holds the conversation */
  }
}

export default function AssistantPanel({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const queries = useQueryClient();
  const [chatId, setChatId] = useState(() => read(CURRENT) || newId());
  const [view, setView] = useState<"chat" | "history">("chat");
  const panel = useRef<HTMLElement>(null);
  const status = useQuery({ queryKey: ["assistant-status"], queryFn: () => api.assistantStatus() });

  // The conversation this panel was showing, as stored: it reopens where it was left.
  // Read once when it is opened and never while it is on screen — the chat holds the
  // live state from then on, and a refetch would replace it mid-answer. No cache either
  // (`gcTime: 0`), so closing and reopening the panel reads what was stored since.
  const stored = useQuery({
    queryKey: ["assistant-session", chatId],
    queryFn: () => api.assistantSession(chatId),
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const [loaded, setLoaded] = useState<string | null>(null);
  if ((stored.isSuccess || stored.isError) && loaded !== chatId) setLoaded(chatId);
  const ready = loaded === chatId;

  const open = (id: string) => {
    // Whatever was cached for it predates its latest answers.
    queries.removeQueries({ queryKey: ["assistant-session", id] });
    write(CURRENT, id);
    setChatId(id);
    setView("chat");
  };

  // F6 moves between the page and the panel, the Windows convention for panes (D62).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "F6") return;
      const inside = panel.current?.contains(document.activeElement) ?? false;
      event.preventDefault();
      if (inside) {
        const main = document.querySelector<HTMLElement>("main");
        const target = main?.querySelector<HTMLElement>("[tabindex],a,button,input") ?? main;
        if (main && !main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
        target?.focus();
      } else {
        panel.current?.querySelector<HTMLElement>("[data-testid=assistant-input]")?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const provider = status.data?.provider ?? "";
  return (
    <aside
      ref={panel}
      data-testid="assistant-panel"
      aria-label={t("assistant.title")}
      className="fixed bottom-24 start-5 z-40 flex h-[min(640px,calc(100vh-8rem))] w-[min(420px,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl bg-surface-1 shadow-2xl ring-1 ring-line"
    >
      <header className="flex items-center gap-1 border-b border-line-subtle px-3 py-2">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{t("assistant.title")}</h2>
        <button
          type="button"
          data-testid="assistant-history"
          aria-pressed={view === "history"}
          onClick={() => setView(view === "history" ? "chat" : "history")}
          className="rounded-sm px-2 py-1 text-xs text-secondary hover:bg-a-200 aria-pressed:bg-a-200"
        >
          {t("assistant.history")}
        </button>
        <button
          type="button"
          data-testid="assistant-new"
          onClick={() => open(newId())}
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
          <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5]" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </header>
      {view === "history" ? (
        <History
          current={chatId}
          onOpen={open}
          onDeleted={(id) => {
            // The list stays open; the chat behind it becomes a new one.
            if (id !== chatId) return;
            const next = newId();
            write(CURRENT, next);
            setChatId(next);
          }}
        />
      ) : ready ? (
        <Chat
          key={chatId}
          id={chatId}
          initial={stored.data?.messages ?? []}
          provider={provider}
          unavailable={status.data?.problem ?? null}
        />
      ) : (
        <div className="grid flex-1 place-items-center">
          <Spinner />
        </div>
      )}
    </aside>
  );
}

type Scope = "meeting" | "all";

function Chat({
  id,
  initial,
  provider,
  unavailable,
}: {
  id: string;
  initial: UIMessage[];
  provider: string;
  unavailable: string | null;
}) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const queries = useQueryClient();
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState<Scope>("meeting");
  const [disclosed, setDisclosed] = useState(() => read(DISCLOSED) === "1");
  const composer = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const meetingId = meetingOf(pathname);
  // A new screen resets the scope to that screen (Granola's model; HAX G4).
  useEffect(() => setScope("meeting"), [meetingId]);
  const effectiveScope: Scope = meetingId && scope === "meeting" ? "meeting" : "all";

  // The screen and the scope go with every question.
  const context = useRef({ route: pathname, meeting_id: meetingId, scope: effectiveScope });
  context.current = { route: pathname, meeting_id: meetingId, scope: effectiveScope };

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
  const { messages, sendMessage, status, stop, error, regenerate } = useChat({
    id,
    messages: initial,
    transport,
    onFinish: () => void queries.invalidateQueries({ queryKey: ["assistant-sessions"] }),
  });
  const busy = status === "submitted" || status === "streaming";
  const [stopped, setStopped] = useState(false);

  useEffect(() => {
    composer.current?.focus();
  }, []);

  const send = (text: string) => {
    text = text.trim();
    if (!text || busy) return;
    setDraft("");
    setStopped(false);
    write(CURRENT, id);
    if (!disclosed) {
      write(DISCLOSED, "1");
      setDisclosed(true);
    }
    void sendMessage({ text });
    // The question goes to the top and stays: the answer is read from its start, not
    // chased to its end as it grows (NN/G).
    window.requestAnimationFrame(() => {
      const questions = log.current?.querySelectorAll<HTMLElement>("[data-testid=assistant-question]");
      questions?.[questions.length - 1]?.scrollIntoView({ block: "start" });
    });
  };

  const last = messages[messages.length - 1];
  const lastAnswer = last?.role === "assistant" ? last : undefined;
  const problem = lastAnswer ? problemOf(lastAnswer) : null;
  const suggestions = !busy && lastAnswer ? suggestionsOf(lastAnswer) : [];

  return (
    <>
      <Announcer status={status} stopped={stopped} error={error?.message ?? ""} answer={lastAnswer} />
      <div
        ref={log}
        role="log"
        aria-label={t("assistant.title")}
        aria-live="off"
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3"
        data-testid="assistant-log"
      >
        {messages.length === 0 && (
          <Empty
            meetingId={meetingId}
            pathname={pathname}
            provider={provider}
            disclosed={disclosed}
            unavailable={unavailable}
            onAsk={send}
          />
        )}
        {messages.map((message) =>
          message.role === "user" ? (
            <Question key={message.id} message={message} />
          ) : (
            <Reply
              key={message.id}
              message={message}
              streaming={busy && message === last}
              isLast={message === last}
              onRetry={() => {
                setStopped(false);
                void regenerate();
              }}
              busy={busy}
            />
          ),
        )}
        {status === "submitted" && (
          <p className="flex items-center gap-2 text-sm text-tertiary">
            <Spinner /> {t("assistant.working")}
          </p>
        )}
        {(error || problem) && (
          <Problem code={problem} text={error?.message ?? ""} provider={provider} onRetry={() => void regenerate()} />
        )}
        {stopped && !busy && (
          <p className="text-xs text-tertiary" data-testid="assistant-stopped">
            {t("assistant.stopped")}{" "}
            <button type="button" className="text-accent underline" onClick={() => void regenerate()}>
              {t("assistant.retry")}
            </button>
          </p>
        )}
        {suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5" data-testid="assistant-suggestions">
            {suggestions.map((item) => (
              <button
                key={item}
                type="button"
                dir="auto"
                data-testid="assistant-suggestion"
                onClick={() => send(item)}
                className="rounded-full border border-line-subtle px-2.5 py-1 text-xs text-secondary hover:bg-a-200"
              >
                {item}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-line-subtle p-3">
        {meetingId && (
          <div className="mb-2 flex" data-testid="assistant-scope" data-scope={effectiveScope}>
            <button
              type="button"
              onClick={() => setScope(effectiveScope === "meeting" ? "all" : "meeting")}
              aria-label={effectiveScope === "meeting" ? t("assistant.scopeWiden") : t("assistant.scopeNarrow")}
              className="flex items-center gap-1 rounded-full bg-a-200 px-2 py-0.5 text-xs text-secondary hover:bg-a-300"
            >
              {effectiveScope === "meeting" ? t("assistant.scopeMeeting") : t("assistant.scopeAll")}
              <span aria-hidden="true">{effectiveScope === "meeting" ? "×" : "+"}</span>
            </button>
          </div>
        )}
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
              send(draft);
            }
          }}
          className="w-full resize-none rounded-md border border-line-subtle bg-raised px-2.5 py-2 text-sm outline-none focus:border-accent"
        />
        <div className="mt-2 flex items-center gap-2">
          <p className="min-w-0 flex-1 text-2xs leading-snug text-tertiary" data-testid="assistant-disclaimer">
            {provider ? t("assistant.sentTo").replace("{provider}", providerName(t, provider)) : ""}
          </p>
          {busy ? (
            <button
              type="button"
              data-testid="assistant-stop"
              onClick={() => {
                setStopped(true);
                void stop();
              }}
              className="rounded-md px-3 py-1.5 text-sm text-secondary hover:bg-a-200"
            >
              {t("assistant.stop")}
            </button>
          ) : (
            <button
              type="button"
              data-testid="assistant-send"
              onClick={() => send(draft)}
              disabled={!draft.trim()}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm text-on-accent disabled:opacity-50"
            >
              {t("assistant.send")}
              <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5] rtl:-scale-x-100" aria-hidden="true">
                <path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * What a screen reader hears: states, not the stream (GitHub Primer's Copilot pattern).
 * Token-by-token text in a live region floods it, so the log is not live; this one
 * region says what is happening, "still working" every five seconds, and when the
 * answer is ready — with how many sources it has.
 */
function Announcer({
  status,
  stopped,
  error,
  answer,
}: {
  status: string;
  stopped: boolean;
  error: string;
  answer: UIMessage | undefined;
}) {
  const { t } = useI18n();
  const [said, setSaid] = useState("");
  const was = useRef(status);
  useEffect(() => {
    const before = was.current;
    was.current = status;
    if (status === "submitted") setSaid(t("assistant.working"));
    else if (status === "ready" && (before === "streaming" || before === "submitted")) {
      if (stopped) setSaid(t("assistant.stopped"));
      else {
        const sources = answer ? citationsOf(answer).size : 0;
        setSaid(t("assistant.ready").replace("{n}", String(sources)));
      }
    } else if (status === "error") setSaid(error);
  }, [status, stopped, error, answer, t]);
  useEffect(() => {
    if (status !== "submitted" && status !== "streaming") return;
    const timer = window.setInterval(() => setSaid((prev) => (prev === t("assistant.stillWorking") ? `${t("assistant.stillWorking")} ` : t("assistant.stillWorking"))), 5000);
    return () => window.clearInterval(timer);
  }, [status, t]);
  return (
    <p role="status" className="sr-only" data-testid="assistant-status">
      {said}
    </p>
  );
}

const PROMPTS_MEETING: MessageKey[] = ["assistant.promptDecided", "assistant.promptOwe", "assistant.promptOpen"];
const PROMPTS_ACTIONS: MessageKey[] = ["assistant.promptWeek", "assistant.promptOverdue", "assistant.promptWho"];
const PROMPTS_ELSEWHERE: MessageKey[] = ["assistant.promptWeek", "assistant.promptLastTalked", "assistant.promptRecent"];

function Empty({
  meetingId,
  pathname,
  provider,
  disclosed,
  unavailable,
  onAsk,
}: {
  meetingId: string;
  pathname: string;
  provider: string;
  disclosed: boolean;
  unavailable: string | null;
  onAsk: (text: string) => void;
}) {
  const { t } = useI18n();
  const prompts = meetingId ? PROMPTS_MEETING : pathname.startsWith("/actions") ? PROMPTS_ACTIONS : PROMPTS_ELSEWHERE;
  return (
    <div className="space-y-3" data-testid="assistant-empty">
      <p className="text-sm text-secondary">{t("assistant.empty")}</p>
      {unavailable ? (
        <Problem code={unavailable} text="" provider={provider} />
      ) : (
        <>
          {!disclosed && provider && (
            <p className="rounded-md bg-a-100 p-2.5 text-xs text-secondary" data-testid="assistant-disclosure">
              {t("assistant.disclosure").replace("{provider}", providerName(t, provider))}{" "}
              <a href="https://upshot.amconsultingai.com/privacy.html" target="_blank" rel="noreferrer" className="text-accent underline">
                {t("assistant.privacy")}
              </a>
            </p>
          )}
          <div className="flex flex-col items-start gap-1.5">
            {prompts.map((key) => (
              <button
                key={key}
                type="button"
                dir="auto"
                data-testid="assistant-prompt"
                onClick={() => onAsk(t(key))}
                className="rounded-md border border-line-subtle px-2.5 py-1.5 text-start text-sm text-secondary hover:bg-a-200"
              >
                {t(key)}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Question({ message }: { message: UIMessage }) {
  const text = message.parts.map((part) => (part.type === "text" ? part.text : "")).join("");
  return (
    <div className="flex scroll-mt-3 justify-end" data-testid="assistant-question">
      <p dir="auto" className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-a-200 px-3 py-2 text-sm">
        {text}
      </p>
    </div>
  );
}

function Reply({
  message,
  streaming,
  isLast,
  busy,
  onRetry,
}: {
  message: UIMessage;
  streaming: boolean;
  isLast: boolean;
  busy: boolean;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const citations = citationsOf(message);
  const text = message.parts.map((part) => (part.type === "text" ? part.text : "")).join("\n\n");
  const meta = (message.metadata ?? {}) as { provider?: string; model?: string };
  const [copied, setCopied] = useState(false);
  return (
    <article className="space-y-2" data-testid="assistant-answer" aria-busy={streaming ? "true" : undefined}>
      <h3 className="sr-only">{t("assistant.title")}</h3>
      {message.parts.map((part, index) =>
        part.type === "dynamic-tool" ? (
          <ToolStep key={index} name={part.toolName} input={part.input} output={"output" in part ? part.output : undefined} state={part.state} />
        ) : null,
      )}
      {text.trim() && (
        <Suspense fallback={<p dir="auto" className="whitespace-pre-wrap text-sm">{text}</p>}>
          <Answer text={text} streaming={streaming} citations={citations} />
        </Suspense>
      )}
      {!streaming && text.trim() && (
        <footer className="flex items-center gap-2 text-2xs text-tertiary" data-testid="assistant-answer-footer">
          <span className="min-w-0 flex-1 truncate" data-testid="assistant-answered-by">
            {meta.provider ? (
              <bdi>
                {t("assistant.answeredBy").replace("{provider}", providerName(t, meta.provider))}
                {meta.model ? ` · ${meta.model}` : ""}
              </bdi>
            ) : null}
          </span>
          <button
            type="button"
            data-testid="assistant-copy"
            onClick={() => {
              void navigator.clipboard?.writeText(text.replace(/\[(\d+)\]\(#cite-\d+\)/g, "[$1]"));
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            }}
            className="rounded-sm px-1.5 py-0.5 hover:bg-a-200"
          >
            {copied ? t("assistant.copied") : t("assistant.copy")}
          </button>
          {isLast && !busy && (
            <button type="button" data-testid="assistant-retry" onClick={onRetry} className="rounded-sm px-1.5 py-0.5 hover:bg-a-200">
              {t("assistant.retry")}
            </button>
          )}
        </footer>
      )}
    </article>
  );
}

const TOOL_LABELS: Record<string, MessageKey> = {
  search: "assistant.tool.search",
  list_meetings: "assistant.tool.list_meetings",
  get_meeting: "assistant.tool.get_meeting",
  get_transcript: "assistant.tool.get_transcript",
  list_action_items: "assistant.tool.list_action_items",
  calendar_range: "assistant.tool.calendar_range",
  related_meetings: "assistant.tool.related_meetings",
};

/** How many things a tool found, read from its output (the JSON between the data tags). */
function countOf(output: unknown): number | null {
  if (typeof output !== "string") return null;
  const start = output.indexOf("{");
  const end = output.lastIndexOf("}");
  if (start < 0 || end < start) return null;
  try {
    const data = JSON.parse(output.slice(start, end + 1)) as Record<string, unknown>;
    if (typeof data.count === "number") return data.count;
    if (Array.isArray(data.lines)) return data.lines.length;
    if (Array.isArray(data.related)) return data.related.length;
    return null;
  } catch {
    // Stored outputs are trimmed; the count, near the start, is often still readable.
    const match = /"count":(\d+)/.exec(output);
    return match ? Number(match[1]) : null;
  }
}

/**
 * One step the assistant took, said exactly ("Searched meetings for 'budget' · 6
 * found"), with its state. A search that found nothing stays open: the empty result is
 * the answer's evidence, and hiding it is how a made-up answer would look honest.
 */
function ToolStep({ name, input, output, state }: { name: string; input: unknown; output: unknown; state: string }) {
  const { t } = useI18n();
  const args = typeof input === "object" && input !== null ? (input as Record<string, unknown>) : {};
  const count = state === "output-available" ? countOf(output) : null;
  const [open, setOpen] = useState(count === 0);
  useEffect(() => {
    if (count === 0) setOpen(true);
  }, [count]);
  const label = t(TOOL_LABELS[name] ?? "assistant.tool.other")
    .replace("{query}", String(args.query ?? args.contains ?? ""))
    .replace("{from}", String(args.date_from ?? ""))
    .replace("{name}", name);
  const done = state === "output-available";
  const failed = state === "output-error";
  const found = count === null ? "" : count === 0 ? t("assistant.foundNone") : t("assistant.found").replace("{n}", String(count));
  const details = Object.entries(args).filter(([, value]) => value !== "" && value !== null && value !== undefined);
  return (
    <div data-testid="assistant-tool" data-tool={name} data-state={state} data-count={count ?? undefined}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 text-start text-xs text-tertiary hover:text-secondary"
      >
        <span className="grid size-3.5 shrink-0 place-items-center" aria-hidden="true">
          {done ? "✓" : failed ? "!" : <Spinner />}
        </span>
        <bdi className="min-w-0 truncate">{label}</bdi>
        {found && <span className="shrink-0">· {found}</span>}
        <svg viewBox="0 0 16 16" className={`ms-auto size-3 shrink-0 fill-none stroke-current stroke-[1.5] ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="M4 6.5 8 10.5l4-4" />
        </svg>
      </button>
      {open && (
        <dl className="mt-1 ms-5.5 space-y-0.5 text-2xs text-tertiary" data-testid="assistant-tool-details">
          {details.map(([key, value]) => (
            <div key={key} className="flex gap-1.5">
              <dt className="font-mono">{key}</dt>
              <dd dir="auto">
                <bdi>{String(value)}</bdi>
              </dd>
            </div>
          ))}
          {count === 0 && <div className="text-secondary">{t("assistant.foundNone")}</div>}
        </dl>
      )}
    </div>
  );
}

/** A problem said in words, with the one thing to do about it (D62). */
function Problem({
  code,
  text,
  provider,
  onRetry,
}: {
  code: string | null;
  text: string;
  provider: string;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  const cli = provider === "codex-subscription" ? "Codex" : "Claude Code";
  const navigate = useNavigate();
  const key: MessageKey | null =
    code === "signed-out"
      ? "assistant.problem.signedOut"
      : code === "not-installed"
        ? "assistant.problem.notInstalled"
        : code === "local-model"
          ? "assistant.problem.localModel"
          : code === "unsupported-provider"
            ? "assistant.problem.unsupported"
            : code === "quota"
                ? "assistant.problem.quota"
                : code === "rate-limited"
                  ? "assistant.problem.rateLimited"
                  : null;
  const toSettings = code !== null && ["signed-out", "not-installed", "local-model", "unsupported-provider", "too-old"].includes(code);
  return (
    <div className="rounded-md bg-danger-quiet/40 p-2.5 text-sm" data-testid="assistant-error" data-problem={code ?? undefined}>
      <p dir="auto" className="text-primary">
        {key ? t(key).replace("{cli}", cli) : text}
      </p>
      {key && text && (
        <p dir="auto" className="mt-1 text-xs text-tertiary">
          {text}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        {toSettings && (
          <button
            type="button"
            data-testid="assistant-to-settings"
            onClick={() => navigate("/settings")}
            className="rounded-md bg-raised px-2.5 py-1 text-xs shadow-[var(--shadow-ring)] hover:bg-a-200"
          >
            {code === "signed-out" ? t("assistant.signIn") : t("assistant.openSettings")}
          </button>
        )}
        {onRetry && !toSettings && (
          <button type="button" onClick={onRetry} className="rounded-md bg-raised px-2.5 py-1 text-xs shadow-[var(--shadow-ring)] hover:bg-a-200">
            {t("assistant.retry")}
          </button>
        )}
      </div>
    </div>
  );
}

/** Today, this week, older: how every chat history is read (Linear, ChatGPT, Claude). */
function groupOf(updatedAt: string, now: Date): "today" | "week" | "older" {
  const when = new Date(updatedAt);
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (when >= startOfDay) return "today";
  const weekAgo = new Date(startOfDay);
  weekAgo.setDate(weekAgo.getDate() - 6);
  return when >= weekAgo ? "week" : "older";
}

const GROUPS: { id: "today" | "week" | "older"; key: MessageKey }[] = [
  { id: "today", key: "assistant.today" },
  { id: "week", key: "assistant.thisWeek" },
  { id: "older", key: "assistant.older" },
];

function History({
  current,
  onOpen,
  onDeleted,
}: {
  current: string;
  onOpen: (id: string) => void;
  onDeleted: (id: string) => void;
}) {
  const { t } = useI18n();
  const queries = useQueryClient();
  const sessions = useQuery({ queryKey: ["assistant-sessions"], queryFn: () => api.assistantSessions() });
  const [editing, setEditing] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const rows = (sessions.data?.sessions ?? []).filter((row) => row.title.toLowerCase().includes(filter.trim().toLowerCase()));
  const now = new Date();

  const refresh = () => void queries.invalidateQueries({ queryKey: ["assistant-sessions"] });
  const rename = async (id: string, title: string) => {
    setEditing(null);
    if (title.trim()) await api.renameAssistantSession(id, title);
    refresh();
  };
  const remove = async (id: string, title: string, after: HTMLElement | null) => {
    const sure = await confirmDialog({
      title: t("assistant.deleteTitle"),
      body: <bdi className="font-semibold">{title}</bdi>,
      confirm: t("assistant.delete"),
      cancel: t("assistant.cancel"),
    });
    if (!sure) return;
    await api.deleteAssistantSession(id);
    onDeleted(id);
    refresh();
    after?.focus();
  };

  if (sessions.isSuccess && (sessions.data?.sessions ?? []).length === 0) {
    return (
      <p className="flex-1 px-3 py-3 text-sm text-tertiary" data-testid="assistant-history-empty">
        {t("assistant.noHistory")}
      </p>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2" data-testid="assistant-history-list">
      <input
        type="search"
        dir="auto"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder={t("assistant.historySearch")}
        aria-label={t("assistant.historySearch")}
        data-testid="assistant-history-search"
        className="mb-2 w-full rounded-md border border-line-subtle bg-raised px-2 py-1 text-sm outline-none focus:border-accent"
      />
      {GROUPS.map((group) => {
        const inGroup = rows.filter((row) => groupOf(row.updated_at, now) === group.id);
        if (inGroup.length === 0) return null;
        return (
          <section key={group.id} className="mb-3">
            <h3 className="px-2 py-1 text-2xs font-medium uppercase tracking-wide text-tertiary">{t(group.key)}</h3>
            <ul>
              {inGroup.map((row, index) => (
                <li key={row.id} className="group flex items-center gap-1 rounded-sm hover:bg-a-200" data-testid="assistant-session" data-session-id={row.id}>
                  {editing === row.id ? (
                    <input
                      autoFocus
                      dir="auto"
                      data-testid="assistant-rename-input"
                      defaultValue={row.title}
                      aria-label={t("assistant.rename")}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void rename(row.id, event.currentTarget.value);
                        if (event.key === "Escape") {
                          event.stopPropagation();
                          setEditing(null);
                        }
                      }}
                      onBlur={(event) => void rename(row.id, event.currentTarget.value)}
                      className="min-w-0 flex-1 rounded-sm border border-accent bg-raised px-2 py-1 text-sm outline-none"
                    />
                  ) : (
                    <button
                      type="button"
                      data-testid="assistant-session-open"
                      aria-current={row.id === current ? "true" : undefined}
                      onClick={() => onOpen(row.id)}
                      className="min-w-0 flex-1 truncate px-2 py-1.5 text-start text-sm aria-[current=true]:font-semibold"
                    >
                      <bdi>{row.title || t("assistant.untitled")}</bdi>
                    </button>
                  )}
                  <button
                    type="button"
                    data-testid="assistant-session-rename"
                    aria-label={`${t("assistant.rename")}: ${row.title}`}
                    onClick={() => setEditing(row.id)}
                    className="rounded-sm px-1.5 py-1 text-2xs text-tertiary opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-a-300"
                  >
                    {t("assistant.rename")}
                  </button>
                  <button
                    type="button"
                    data-testid="assistant-session-delete"
                    aria-label={`${t("assistant.delete")}: ${row.title}`}
                    onClick={(event) => {
                      const list = event.currentTarget.closest("ul");
                      const previous = list?.querySelectorAll<HTMLElement>("[data-testid=assistant-session-open]")[Math.max(0, index - 1)] ?? null;
                      void remove(row.id, row.title, previous);
                    }}
                    className="rounded-sm px-1.5 py-1 text-2xs text-danger opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-a-300"
                  >
                    {t("assistant.delete")}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function citationsOf(message: UIMessage): Map<number, Citation> {
  const citations = new Map<number, Citation>();
  for (const part of message.parts) {
    if (part.type === "data-citation") {
      const citation = part.data as Citation;
      citations.set(citation.n, citation);
    }
  }
  return citations;
}

function suggestionsOf(message: UIMessage): string[] {
  for (const part of message.parts) {
    if (part.type === "data-suggestions") {
      const items = (part.data as { items?: unknown }).items;
      return Array.isArray(items) ? items.map(String).slice(0, 3) : [];
    }
  }
  return [];
}

function problemOf(message: UIMessage): string | null {
  for (const part of message.parts) {
    if (part.type === "data-problem") return String((part.data as { code?: unknown }).code ?? "") || null;
  }
  return null;
}

const PROVIDER_NAMES: Record<string, MessageKey> = {
  "claude-subscription": "assistant.provider.claude",
  "codex-subscription": "assistant.provider.codex",
  anthropic: "assistant.provider.anthropic",
  openai: "assistant.provider.openai",
  gemini: "assistant.provider.gemini",
  ollama: "assistant.provider.ollama",
  fake: "assistant.provider.fake",
};

function providerName(t: (key: MessageKey) => string, provider: string): string {
  const key = PROVIDER_NAMES[provider];
  return key ? t(key) : provider;
}

function meetingOf(pathname: string): string {
  const match = pathname.match(/^\/m\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function newId(): string {
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
