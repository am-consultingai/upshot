import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type SearchHit } from "../api";
import { useI18n } from "../i18n";
import { forgetSearches, recentSearches, rememberSearch } from "../lib/recents";
import { formatClock, formatDurationShort } from "../lib/format";
import type { MessageKey } from "../locales/en";

type Scope = "all" | SearchHit["kind"];

const SCOPES: { id: Scope; key: MessageKey }[] = [
  { id: "all", key: "search.scopeAll" },
  { id: "title", key: "search.scopeMeetings" },
  { id: "action", key: "search.scopeActions" },
  { id: "transcript", key: "search.scopeTranscripts" },
];

const GROUP: Record<SearchHit["kind"], MessageKey> = {
  title: "search.scopeMeetings",
  action: "search.scopeActions",
  transcript: "search.scopeTranscripts",
};

/**
 * Search across meeting names, action items and transcripts.
 *
 * The long form of what the palette finds: the same index, the same groups, with
 * room for every hit rather than the first dozen. Before anything is typed it is not
 * an empty page with a hint in it — it offers what you searched for last, the
 * meetings you are most likely to be looking for, and the scopes, which is what
 * Linear and Raycast put in the same place.
 *
 * The hit is the unit, carrying who said it, when, and the line it appeared in: the
 * point of searching a meeting you sat in is "show me the sentence". SQLite's own
 * `snippet()` marks the term with brackets rather than HTML, which is what lets it be
 * rendered as text instead of injected as markup.
 */
export default function SearchPage() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [term, setTerm] = useState(() => params.get("q") ?? "");
  const [scope, setScope] = useState<Scope>("all");
  // Read on every render rather than once: a search remembered a moment ago has to be
  // there when the field is cleared. It is five strings from local storage.
  const [, setForgotten] = useState(0);
  const recents = recentSearches();
  const input = useRef<HTMLInputElement | null>(null);
  const ready = term.trim().length > 1;

  useEffect(() => {
    input.current?.focus();
  }, []);
  // The query is in the address, so a search can be returned to with Back.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if ((params.get("q") ?? "") !== term.trim()) {
        setParams(term.trim() ? { q: term.trim() } : {}, { replace: true });
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [term, params, setParams]);

  const results = useQuery({
    queryKey: ["search", term.trim()],
    queryFn: () => api.search(term.trim()),
    enabled: ready,
    placeholderData: (previous) => previous,
  });
  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings(), enabled: !ready });

  const hits = ready ? (results.data?.hits ?? []) : [];
  const count = (kind: Scope) => (kind === "all" ? hits.length : hits.filter((hit) => hit.kind === kind).length);
  // A hit's kind decides how its row reads, so group by it: a meeting name is a
  // stronger answer than a sentence and should not be buried among sentences.
  const groups = (["title", "action", "transcript"] as const)
    .filter((kind) => scope === "all" || scope === kind)
    .map((kind) => [kind, hits.filter((hit) => hit.kind === kind)] as const)
    .filter(([, rows]) => rows.length > 0);

  const open = (hit: SearchHit) => {
    rememberSearch(term);
    /*
     * Lands on the moment, not the top of the meeting. Every turn carries `at_ms`
     * and the meeting page already seeks from a transcript click; carrying it in the
     * URL is the whole of the work.
     */
    navigate(hit.kind === "transcript" ? `/m/${hit.meeting_id}?at=${hit.at_ms}` : `/m/${hit.meeting_id}`);
  };

  return (
    <section data-testid="search-page">
      <h1 className="display mb-4 text-2xl">{t("nav.search")}</h1>
      <label
        data-testid="search-field"
        className="flex h-10 items-center gap-2.5 rounded-lg bg-surface-1 px-3 shadow-[var(--shadow-ring-subtle)] focus-within:shadow-[0_0_0_1px_var(--accent)]"
      >
        <svg viewBox="0 0 16 16" className="size-4 shrink-0 fill-none stroke-current stroke-[1.5] text-tertiary">
          <path d="M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z" />
        </svg>
        <input
          ref={input}
          data-testid="search-input"
          value={term}
          placeholder={t("search.placeholder")}
          onChange={(event) => setTerm(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") rememberSearch(term);
            if (event.key === "Escape") setTerm("");
          }}
          className="no-focus-ring min-w-0 flex-1 bg-transparent text-md outline-none placeholder:text-tertiary"
        />
        {ready && results.data && (
          <span data-testid="search-count" className="shrink-0 font-mono text-2xs text-tertiary tabular-nums">
            {results.data.count} {results.data.count === 1 ? t("search.countOne") : t("search.countMany")}
          </span>
        )}
        <kbd className="shrink-0 rounded-[3px] px-1 font-mono text-[10px] text-tertiary shadow-[var(--shadow-ring-subtle)]">
          /
        </kbd>
      </label>

      <div data-testid="search-scopes" className="mt-3 flex w-max gap-0.5 rounded-md bg-surface-3 p-0.5" role="group">
        {SCOPES.map((option) => (
          <button
            key={option.id}
            type="button"
            data-testid={`search-scope-${option.id}`}
            aria-pressed={scope === option.id}
            onClick={() => setScope(option.id)}
            className={`flex h-6 items-center gap-1.5 rounded-xs px-2.5 text-xs ${
              scope === option.id
                ? "bg-raised text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]"
                : "text-secondary hover:bg-a-200 hover:text-primary"
            }`}
          >
            {t(option.key)}
            {ready && (
              <span className="font-mono text-2xs text-tertiary tabular-nums">{count(option.id)}</span>
            )}
          </button>
        ))}
      </div>

      {!ready && (
        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <div data-testid="search-recents">
            <h2 className="mb-1.5 flex items-center text-2xs uppercase tracking-wide text-tertiary">
              {t("search.recentSearches")}
              {recents.length > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    forgetSearches();
                    setForgotten((count) => count + 1);
                  }}
                  className="ms-auto rounded-sm px-1 text-2xs normal-case tracking-normal text-tertiary hover:bg-a-200 hover:text-primary"
                >
                  {t("search.clearRecents")}
                </button>
              )}
            </h2>
            {recents.length === 0 ? (
              <p data-testid="search-hint" className="text-sm text-tertiary">
                {t("search.hint")}
              </p>
            ) : (
              <ul>
                {recents.map((recent) => (
                  <li key={recent}>
                    <button
                      type="button"
                      data-testid="search-recent"
                      onClick={() => setTerm(recent)}
                      className="-mx-2 flex h-8 w-[calc(100%+1rem)] items-center gap-2 rounded-md px-2 text-start text-sm text-secondary hover:bg-a-200 hover:text-primary"
                    >
                      <svg viewBox="0 0 16 16" className="size-3.5 shrink-0 fill-none stroke-current stroke-[1.5] text-tertiary">
                        <path d="M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0ZM8 5v3.2l2 1.2" />
                      </svg>
                      <span className="truncate">{recent}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div data-testid="search-recent-meetings">
            <h2 className="mb-1.5 text-2xs uppercase tracking-wide text-tertiary">{t("search.recentMeetings")}</h2>
            <ul>
              {(meetings.data?.meetings ?? []).slice(0, 5).map((meeting) => (
                <li key={meeting.id}>
                  <Link
                    to={`/m/${meeting.id}`}
                    className="-mx-2 flex h-8 items-center gap-2 rounded-md px-2 text-sm hover:bg-a-200"
                  >
                    <span className="min-w-0 flex-1 truncate">{meeting.title ?? meeting.id}</span>
                    <span className="shrink-0 font-mono text-2xs text-tertiary tabular-nums">
                      {new Date(meeting.started_at).toLocaleDateString(locale, { day: "numeric", month: "short" })}{" "}
                      {formatClock(meeting.started_at)}
                      {formatDurationShort(meeting.duration_s) && ` · ${formatDurationShort(meeting.duration_s)}`}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {ready && results.isLoading && <p className="mt-4 text-sm text-tertiary">{t("common.loading")}</p>}
      {ready && !results.isLoading && groups.length === 0 && (
        <p data-testid="search-none" className="mt-4 text-sm text-tertiary">
          {t("search.none")}
        </p>
      )}

      <div data-testid="search-results" className="mt-4">
        {groups.map(([kind, rows]) => (
          <section key={kind} data-testid="search-group" data-kind={kind} className="mb-5">
            <h2 className="mb-1 flex items-center gap-2 border-b border-line-subtle pb-1.5 text-2xs uppercase tracking-wide text-tertiary">
              {t(GROUP[kind])}
              <span className="font-mono tabular-nums">{rows.length}</span>
            </h2>
            <ul>
              {rows.map((hit, index) => (
                <li key={`${hit.meeting_id}-${hit.at_ms}-${index}`}>
                  <button
                    type="button"
                    data-testid="search-result"
                    data-meeting-id={hit.meeting_id}
                    data-at-ms={hit.at_ms}
                    onClick={() => open(hit)}
                    className="-mx-2 block w-[calc(100%+1rem)] rounded-md px-2 py-2 text-start hover:bg-a-200 active:bg-a-300"
                  >
                    <Snippet snippet={hit.snippet} />
                    <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-2xs text-tertiary">
                      <span data-testid="hit-meeting" className="truncate text-secondary">
                        {hit.meeting_title ?? hit.meeting_id}
                      </span>
                      <span className="opacity-50">·</span>
                      {/*
                       * A title or action hit has no speaker and no moment to seek to,
                       * so it says what it is instead of claiming somebody said it.
                       */}
                      <span data-testid="hit-kind" data-kind={hit.kind}>
                        {hit.kind === "title"
                          ? t("search.kindTitle")
                          : hit.kind === "action"
                            ? t("search.kindAction")
                            : hit.speaker === "ME"
                              ? t("meeting.you")
                              : (hit.speaker_name ?? t("meeting.themSaid"))}
                      </span>
                      {hit.kind === "transcript" && (
                        <>
                          <span className="opacity-50">·</span>
                          <span className="font-mono tabular-nums">{clock(hit.at_ms)}</span>
                        </>
                      )}
                      {hit.meeting_started_at && (
                        <>
                          <span className="opacity-50">·</span>
                          <span className="tabular-nums">
                            {new Date(hit.meeting_started_at).toLocaleDateString(locale, {
                              day: "numeric",
                              month: "short",
                            })}
                          </span>
                        </>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}

/** mm:ss into the recording. */
function clock(atMs: number): string {
  const total = Math.max(0, Math.round(atMs / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * The matched line, with the term standing out.
 *
 * `snippet()` wraps the match in `[` and `]`, so this splits on those rather than
 * setting HTML — the transcript is the user's own speech and never becomes markup.
 */
export function splitSnippet(snippet: string): { text: string; hit: boolean }[] {
  const parts: { text: string; hit: boolean }[] = [];
  const pattern = /\[([^\]]*)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(snippet)) !== null) {
    if (match.index > last) parts.push({ text: snippet.slice(last, match.index), hit: false });
    parts.push({ text: match[1], hit: true });
    last = pattern.lastIndex;
  }
  if (last < snippet.length) parts.push({ text: snippet.slice(last), hit: false });
  return parts;
}

function Snippet({ snippet }: { snippet: string }) {
  return (
    <span data-testid="hit-snippet" className="block text-sm text-primary">
      {splitSnippet(snippet).map((part, index) =>
        part.hit ? (
          <mark key={index} className="rounded-xs bg-accent-quiet px-0.5 text-primary">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </span>
  );
}
