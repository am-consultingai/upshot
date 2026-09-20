import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../i18n";

/**
 * Search across every transcript, answering with sentences rather than titles.
 *
 * This used to list matching meeting names and nothing else: four identical-looking
 * rows, each of which had to be opened to find out whether it was the right one.
 * The point of searching a meeting you sat in is "show me the sentence" — so the
 * hit is the unit here, carrying who said it, when, and the line it appeared in.
 *
 * SQLite's own `snippet()` marks the term with brackets rather than HTML, which is
 * what lets it be rendered as text instead of injected as markup.
 */
export default function SearchPage() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const ready = term.trim().length > 1;

  const results = useQuery({
    queryKey: ["search", term.trim()],
    queryFn: () => api.search(term.trim()),
    enabled: ready,
  });

  const hits = results.data?.hits ?? [];

  return (
    <section data-testid="search-page">
      <input
        data-testid="search-input"
        value={term}
        placeholder={t("search.placeholder")}
        onChange={(event) => setTerm(event.target.value)}
        className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-md outline-none focus:border-accent"
      />

      <div className="mt-4 flex items-baseline gap-2">
        <h2 className="text-xs font-medium text-tertiary">{t("search.results")}</h2>
        {ready && results.data && (
          <span data-testid="search-count" className="text-2xs text-tertiary tabular-nums">
            {results.data.count} {results.data.count === 1 ? t("search.countOne") : t("search.countMany")}
          </span>
        )}
      </div>

      {!ready && (
        <p data-testid="search-hint" className="mt-2 text-sm text-tertiary">
          {t("search.hint")}
        </p>
      )}
      {ready && results.isLoading && (
        <p className="mt-2 text-sm text-tertiary">{t("common.loading")}</p>
      )}
      {ready && !results.isLoading && hits.length === 0 && (
        <p data-testid="search-none" className="mt-2 text-sm text-tertiary">
          {t("search.none")}
        </p>
      )}

      <ul data-testid="search-results" className="mt-1">
        {hits.map((hit, index) => (
          <li key={`${hit.meeting_id}-${hit.at_ms}-${index}`}>
            <button
              type="button"
              data-testid="search-result"
              data-meeting-id={hit.meeting_id}
              data-at-ms={hit.at_ms}
              /*
               * Lands on the moment, not the top of the meeting. Every turn carries
               * `at_ms` and the meeting page already seeks from a transcript click;
               * carrying it in the URL is the whole of the work.
               */
              onClick={() => navigate(`/m/${hit.meeting_id}?at=${hit.at_ms}`)}
              className="block w-full rounded-md px-2 py-2 text-start hover:bg-surface-2"
            >
              <Snippet snippet={hit.snippet} />
              <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-2xs text-tertiary">
                <span data-testid="hit-meeting" className="truncate text-secondary">
                  {hit.meeting_title ?? hit.meeting_id}
                </span>
                <span className="opacity-50">·</span>
                <span data-testid="hit-speaker">
                  {hit.speaker === "ME" ? t("meeting.meSaid") : t("meeting.themSaid")}
                </span>
                <span className="opacity-50">·</span>
                <span className="tabular-nums">{clock(hit.at_ms)}</span>
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
