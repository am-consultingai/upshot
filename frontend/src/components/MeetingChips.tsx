import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MeetingDetail } from "../api";
import { useI18n } from "../i18n";
import { formatClock, formatDurationShort, formatShortDate } from "../lib/format";
import Tooltip from "./Tooltip";

/**
 * The meeting's facts, as a row of outlined chips under its title: when, how long,
 * who, and what it is filed under.
 *
 * This replaces a bordered panel that opened with the words "From your calendar:"
 * and stacked the date, the attendees and two buttons down the page. Circleback,
 * Fireflies and Granola each arrived independently at a chip row here: it is compact,
 * every chip is one fact, and it mirrors cleanly in RTL because nothing is
 * positioned, only ordered.
 *
 * The people chip is also the way into the invitation — who exactly was invited,
 * the agenda, and the "this is the wrong event" escape hatch — which used to be a
 * strip of its own between the title and anything worth reading.
 *
 * Tags are the user's own words for what a meeting is about. A tag is a coloured dot
 * and a name; "+ Add tag" is dashed because it is a place, not a thing.
 */
export default function MeetingChips({
  meeting,
  onPeople,
}: {
  meeting: MeetingDetail;
  /** Opens the invitation and the calendar match. */
  onPeople?: () => void;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const calendar = meeting.calendar;
  const people = calendar?.participants ?? [];
  const more = calendar?.participants_more ?? 0;
  const tags = meeting.tags ?? [];
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const input = useRef<HTMLInputElement | null>(null);

  const known = useQuery({ queryKey: ["tags"], queryFn: api.tags, enabled: adding });
  const save = useMutation({
    mutationFn: (next: string[]) => api.putTags(meeting.id, next),
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey: ["meeting", meeting.id] });
      queryClient.setQueryData(["meeting", meeting.id], (old: MeetingDetail | undefined) =>
        old ? { ...old, tags: next } : old,
      );
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["meeting", meeting.id] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
      void queryClient.invalidateQueries({ queryKey: ["tags"] });
    },
  });

  const add = (tag: string) => {
    const clean = tag.trim().replace(/\s+/g, " ");
    setDraft("");
    setAdding(false);
    if (!clean || tags.some((existing) => existing.toLowerCase() === clean.toLowerCase())) return;
    save.mutate([...tags, clean]);
  };
  const suggestions = (known.data?.tags ?? [])
    .map((row) => row.tag)
    .filter((tag) => !tags.some((existing) => existing.toLowerCase() === tag.toLowerCase()))
    .filter((tag) => tag.toLowerCase().includes(draft.trim().toLowerCase()))
    .slice(0, 6);

  const when = new Date(calendar?.event?.start ?? meeting.started_at);
  const length = formatDurationShort(meeting.duration_s);
  const count = people.length + more;

  return (
    <div data-testid="meeting-chips" className="mb-5 flex flex-wrap items-center gap-1.5">
      <Chip icon={CALENDAR} testid="chip-when" title={`${formatShortDate(when, locale)} · ${formatClock(when.toISOString())}`}>
        <bdi>{formatShortDate(when, locale)}</bdi>
      </Chip>

      {length && (
        <Chip icon={CLOCK} testid="chip-duration">
          {length}
        </Chip>
      )}

      {count > 0 && (
        <Tooltip label={t(count === 1 ? "meeting.personOne" : "meeting.people").replace("{n}", String(count))} hint={t("help.people")}>
        <Chip
          icon={PEOPLE}
          testid="chip-people"
          onClick={onPeople}
        >
          {t(count === 1 ? "meeting.personOne" : "meeting.people").replace("{n}", String(count))}
        </Chip>
        </Tooltip>
      )}

      {tags.map((tag) => (
        <span
          key={tag}
          data-testid="chip-tag"
          data-tag={tag}
          className="group inline-flex h-6 items-center gap-1.5 rounded-full ps-2.5 pe-1.5 text-xs text-secondary shadow-[var(--shadow-ring)]"
        >
          <span aria-hidden="true" className="size-1.5 rounded-full" style={{ background: tagColour(tag) }} />
          <bdi>{tag}</bdi>
          <button
            type="button"
            data-testid="chip-tag-remove"
            aria-label={t("meeting.removeTag").replace("{tag}", tag)}
            title={t("meeting.removeTag").replace("{tag}", tag)}
            onClick={() => save.mutate(tags.filter((existing) => existing !== tag))}
            className="grid size-4 place-items-center rounded-full text-tertiary opacity-0 transition-opacity group-hover:opacity-100 hover:bg-a-200 hover:text-primary focus-visible:opacity-100"
          >
            <svg viewBox="0 0 16 16" className="size-2.5 fill-none stroke-current stroke-[2]">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </span>
      ))}

      {adding ? (
        <span className="relative">
          <input
            ref={input}
            data-testid="tag-input"
            autoFocus
            value={draft}
            maxLength={40}
            aria-label={t("meeting.addTag")}
            placeholder={t("meeting.tagPlaceholder")}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => window.setTimeout(() => add(input.current?.value ?? ""), 120)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add(draft);
              }
              if (event.key === "Escape") {
                setDraft("");
                setAdding(false);
              }
            }}
            className="no-focus-ring h-6 w-32 rounded-full bg-transparent px-2.5 text-xs shadow-[0_0_0_1px_var(--accent)] outline-none"
          />
          {suggestions.length > 0 && (
            <ul
              data-testid="tag-suggestions"
              className="absolute top-7 z-30 w-44 rounded-lg bg-raised p-1 shadow-[var(--shadow-ring),var(--shadow-md),var(--shadow-edge)]"
              style={{ insetInlineStart: 0 }}
            >
              {suggestions.map((tag) => (
                <li key={tag}>
                  <button
                    type="button"
                    onMouseDown={(event) => {
                      event.preventDefault();
                      add(tag);
                    }}
                    className="flex h-7 w-full items-center gap-2 rounded-md px-2 text-start text-sm hover:bg-a-200"
                  >
                    <span className="size-1.5 rounded-full" style={{ background: tagColour(tag) }} />
                    {tag}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </span>
      ) : (
        <Tooltip label={t("meeting.addTag")} hint={t("help.addTag")}>
        <button
          type="button"
          data-testid="chip-add-tag"
          onClick={() => setAdding(true)}
          className="inline-flex h-6 items-center rounded-full border border-dashed border-line-strong px-2.5 text-xs text-tertiary hover:border-solid hover:bg-a-200 hover:text-primary"
        >
          + {t("meeting.addTag")}
        </button>
        </Tooltip>
      )}
    </div>
  );
}

/** A tag's colour, from its name, so "Growth" is the same violet everywhere it appears. */
export function tagColour(tag: string): string {
  let hash = 0;
  for (const char of tag.toLowerCase()) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `var(--speaker-${(hash % 5) + 1})`;
}

function Chip({
  icon,
  children,
  testid,
  title,
  onClick,
  ...rest
}: {
  icon: string;
  children: React.ReactNode;
  testid?: string;
  title?: string;
  onClick?: () => void;
} & React.HTMLAttributes<HTMLElement>) {
  const className =
    "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-xs text-secondary shadow-[var(--shadow-ring)]";
  const body = (
    <>
      <svg viewBox="0 0 16 16" className="size-3 shrink-0 fill-none stroke-current stroke-[1.6] text-tertiary">
        <path d={icon} />
      </svg>
      {children}
    </>
  );
  if (onClick) {
    return (
      <button
        {...rest}
        type="button"
        data-testid={testid}
        title={title}
        onClick={onClick}
        className={`${className} hover:bg-a-200 hover:text-primary active:bg-a-300`}
      >
        {body}
      </button>
    );
  }
  return (
    <span {...rest} data-testid={testid} title={title} className={className}>
      {body}
    </span>
  );
}

const CALENDAR = "M2.5 3.5h11v10h-11zM2.5 6.5h11M5.5 2v2M10.5 2v2";
const CLOCK = "M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0ZM8 5v3.2l2 1.2";
const PEOPLE =
  "M8.4 6a2.4 2.4 0 1 1-4.8 0 2.4 2.4 0 0 1 4.8 0ZM1.8 13c.4-2.2 2.1-3.4 4.2-3.4s3.8 1.2 4.2 3.4M11 4.2a2.2 2.2 0 0 1 0 4M12 9.8c1.4.4 2.3 1.5 2.5 3.2";
