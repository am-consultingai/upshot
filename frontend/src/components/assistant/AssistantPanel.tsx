import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { api, csrfToken } from "../../api";
import { confirmDialog } from "../ConfirmDialog";
import { useI18n, type MessageKey } from "../../i18n";
import { Spinner } from "../BusyButton";
import { stamp } from "../../lib/speakers";

/** What the server sends for each checked citation (app/assistant/citations.py). */
export interface Citation {
  n: number;
  meeting_id: string;
  title: string;
  started_at: string | null;
  at_ms: number | null;
  speaker: string;
  quote: string;
}

/**
 * The assistant (D61, D62): one conversation about the user's meetings and about
 * Upshot, from any screen. A docked column beside the content rather than a sheet over
 * it — the meeting it is asked about stays in view, and a citation opens beside it.
 *
 * The answer comes from the user's own CLI (Claude Code on their plan), which calls
 * Upshot's read-only tools; this component only shows the stream.
 */
const CURRENT = "upshot.assistant.chat";

function remembered(): string {
  try {
    return window.localStorage.getItem(CURRENT) ?? "";
  } catch {
    return "";
  }
}

function remember(id: string) {
  try {
    window.localStorage.setItem(CURRENT, id);
  } catch {
    /* private window: the conversation is still in the database */
  }
}

export default function AssistantPanel({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [chatId, setChatId] = useState(() => remembered() || newId());
  const [view, setView] = useState<"chat" | "history">("chat");
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

  const queries = useQueryClient();
  const open = (id: string) => {
    // Whatever was cached for it predates its latest answers.
    queries.removeQueries({ queryKey: ["assistant-session", id] });
    remember(id);
    setChatId(id);
    setView("chat");
  };

  return (
    <aside
      data-testid="assistant-panel"
      aria-label={t("assistant.title")}
      className="flex w-[25rem] shrink-0 flex-col border-s border-line-subtle bg-surface-1"
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
          <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5]">
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
            remember(next);
            setChatId(next);
          }}
        />
      ) : ready ? (
        <Chat key={chatId} id={chatId} initial={stored.data?.messages ?? []} />
      ) : (
        <div className="grid flex-1 place-items-center">
          <Spinner />
        </div>
      )}
    </aside>
  );
}

function Chat({ id, initial }: { id: string; initial: UIMessage[] }) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const queries = useQueryClient();
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
  const { messages, sendMessage, status, stop, error } = useChat({
    id,
    messages: initial,
    transport,
    onFinish: () => void queries.invalidateQueries({ queryKey: ["assistant-sessions"] }),
  });
  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    composer.current?.focus();
  }, []);

  const send = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    remember(id);
    void sendMessage({ text });
  };

  return (
    <>
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
    </>
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
  const rows = sessions.data?.sessions ?? [];
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

  if (sessions.isSuccess && rows.length === 0) {
    return (
      <p className="flex-1 px-3 py-3 text-sm text-tertiary" data-testid="assistant-history-empty">
        {t("assistant.noHistory")}
      </p>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2" data-testid="assistant-history-list">
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
  const citations = new Map<number, Citation>();
  for (const part of message.parts) {
    if (part.type === "data-citation") {
      const citation = part.data as Citation;
      citations.set(citation.n, citation);
    }
  }
  return (
    <div className="space-y-2" data-testid="assistant-answer">
      {message.parts.map((part, index) => {
        if (part.type === "text") {
          return (
            <p key={index} dir="auto" className="whitespace-pre-wrap text-sm leading-relaxed">
              <Cited text={part.text} citations={citations} />
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

/** Text with its `[n](#cite-n)` links drawn as numbered chips. */
function Cited({ text, citations }: { text: string; citations: Map<number, Citation> }) {
  const pieces = text.split(/\[(\d+)\]\(#cite-\d+\)/);
  return (
    <>
      {pieces.map((piece, index) => {
        if (index % 2 === 0) return piece;
        const citation = citations.get(Number(piece));
        return citation ? <CitationChip key={index} citation={citation} /> : null;
      })}
    </>
  );
}

function CitationChip({ citation }: { citation: Citation }) {
  const navigate = useNavigate();
  const { t } = useI18n();
  const where = citation.at_ms === null ? "" : ` · ${stamp(citation.at_ms / 1000)}`;
  const label = `${t("assistant.source")} ${citation.n}: ${citation.title}${where}`;
  return (
    <bdi>
      <button
        type="button"
        data-testid="assistant-citation"
        data-meeting-id={citation.meeting_id}
        data-at-ms={citation.at_ms ?? ""}
        aria-label={label}
        title={citation.quote ? `${citation.speaker ? `${citation.speaker}: ` : ""}“${citation.quote}”\n${label}` : label}
        onClick={() =>
          navigate(citation.at_ms === null ? `/m/${citation.meeting_id}` : `/m/${citation.meeting_id}?at=${citation.at_ms}`)
        }
        className="mx-0.5 inline-grid h-4.5 min-w-4.5 place-items-center rounded-xs bg-a-200 px-1 align-text-top font-mono text-2xs text-accent hover:bg-a-300"
      >
        {citation.n}
      </button>
    </bdi>
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

function ToolStep({ name, input, state }: { name: string; input: unknown; state: string }) {
  const { t } = useI18n();
  const args = typeof input === "object" && input !== null ? (input as Record<string, unknown>) : {};
  const label = t(TOOL_LABELS[name] ?? "assistant.tool.other")
    .replace("{query}", String(args.query ?? args.contains ?? ""))
    .replace("{from}", String(args.date_from ?? ""))
    .replace("{name}", name);
  const done = state === "output-available";
  const failed = state === "output-error";
  return (
    <p className="flex items-center gap-2 text-xs text-tertiary" data-testid="assistant-tool" data-tool={name} data-state={state}>
      {done ? "✓" : failed ? "!" : <Spinner />}
      <bdi>{label}</bdi>
    </p>
  );
}

function meetingOf(pathname: string): string {
  const match = pathname.match(/^\/m\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function newId(): string {
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
