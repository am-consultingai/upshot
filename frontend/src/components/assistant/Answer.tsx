import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Streamdown, type Components } from "streamdown";
import { useI18n } from "../../i18n";
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
  /** Set when the conversation is reopened and the meeting has been deleted since. */
  missing?: boolean;
}

/**
 * An answer's Markdown, loaded only with the panel (it is the heaviest thing in it).
 *
 * Streamdown because it is built for text that is still arriving: half-finished
 * Markdown is repaired as it streams, blocks are memoised, and `dir="auto"` sets the
 * direction per paragraph — one answer can mix Hebrew and English (D62).
 *
 * Links are Upshot's own or nothing (D61): `#cite-n` is a citation chip, `/…` is a
 * route in the app, anything else is shown as its text. Images are never fetched —
 * a remote image is how an injected instruction would send data out — only named.
 */
export default function Answer({
  text,
  streaming,
  citations,
}: {
  text: string;
  streaming: boolean;
  citations: Map<number, Citation>;
}) {
  const components: Components = {
    a: ({ href, children }) => {
      const cite = /^#cite-(\d+)$/.exec(href ?? "");
      if (cite) {
        const citation = citations.get(Number(cite[1]));
        return citation ? <CitationChip citation={citation} /> : null;
      }
      if (href && href.startsWith("/") && !href.startsWith("//")) {
        return (
          <Link to={href} className="text-accent underline underline-offset-2">
            {children}
          </Link>
        );
      }
      return <span>{children}</span>;
    },
    img: ({ alt }) => (alt ? <span className="text-tertiary">[{alt}]</span> : null),
  };
  return (
    <Streamdown
      dir="auto"
      mode={streaming ? "streaming" : "static"}
      parseIncompleteMarkdown
      controls={false}
      linkSafety={{ enabled: false }}
      components={components}
      className="assistant-prose text-sm leading-relaxed"
    >
      {text}
    </Streamdown>
  );
}

/**
 * A numbered source. Hover or focus shows the words it points at, so checking the
 * answer costs a glance rather than a trip (NN/G on over-trust); a click opens the
 * meeting at that moment. A source whose meeting is gone says so instead of leading
 * to nothing.
 */
export function CitationChip({ citation }: { citation: Citation }) {
  const navigate = useNavigate();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const time = citation.at_ms === null ? "" : stamp(citation.at_ms / 1000);
  const label = citation.missing
    ? `${t("assistant.source")} ${citation.n}: ${t("assistant.sourceMissing")}`
    : `${t("assistant.source")} ${citation.n}: ${citation.title}${time ? ` · ${time}` : ""}`;
  return (
    <bdi className="relative inline-block">
      <button
        type="button"
        data-testid="assistant-citation"
        data-meeting-id={citation.meeting_id}
        data-at-ms={citation.at_ms ?? ""}
        data-missing={citation.missing ? "true" : undefined}
        aria-label={label}
        aria-disabled={citation.missing ? "true" : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => {
          if (citation.missing) return;
          navigate(citation.at_ms === null ? `/m/${citation.meeting_id}` : `/m/${citation.meeting_id}?at=${citation.at_ms}`);
        }}
        className={`mx-0.5 inline-grid h-4.5 min-w-4.5 place-items-center rounded-xs px-1 align-text-top font-mono text-2xs ${
          citation.missing ? "bg-a-100 text-tertiary line-through" : "bg-a-200 text-accent hover:bg-a-300"
        }`}
      >
        {citation.n}
      </button>
      {open && (
        <span
          role="tooltip"
          data-testid="assistant-citation-card"
          className="absolute bottom-full end-0 z-[75] mb-1.5 block w-72 rounded-md bg-raised p-2.5 text-start text-xs shadow-[var(--shadow-ring),var(--shadow-md),var(--shadow-edge)]"
        >
          {citation.missing ? (
            <span className="text-tertiary">{t("assistant.sourceMissing")}</span>
          ) : (
            <>
              {citation.quote && (
                <span dir="auto" className="mb-1.5 block text-sm text-primary">
                  “{citation.quote}”
                </span>
              )}
              <span className="block text-tertiary">
                {citation.speaker && (
                  <>
                    <bdi>{citation.speaker}</bdi> ·{" "}
                  </>
                )}
                <bdi className="font-medium text-secondary">{citation.title}</bdi>
                {time && (
                  <>
                    {" "}· <span dir="ltr" className="font-mono">{time}</span>
                  </>
                )}
              </span>
            </>
          )}
        </span>
      )}
    </bdi>
  );
}
