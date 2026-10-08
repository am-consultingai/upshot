import { useEffect, useMemo, useRef, useState } from "react";
import { openFeedback } from "./FeedbackDialog";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRecordingControls } from "../lib/recording";
import { api, type SearchHit } from "../api";
import { useI18n } from "../i18n";
import { hitSpeaker } from "../lib/speakers";
import { boost, score } from "../lib/score";
import { applyTheme, type Theme } from "../theme";
import type { MessageKey } from "../locales/en";
import { formatOffset } from "../lib/format";
import { splitSnippet } from "../routes/Search";
import { rememberSearch } from "../lib/recents";
import { meetingKey, type MeetingKey } from "../lib/meetingKeys";
import { DETECTION_MODES } from "../lib/detection";

/*
 * One glyph per command, so the list can be scanned by shape rather than read line
 * by line. The sidebar's own icons where it has one — the same 16px grid and 1.5
 * stroke — so a destination looks the same in both places.
 */
const ICONS = {
  calendar: "M2.5 4.5h11v9h-11zM2.5 7h11M5.5 2.5v2M10.5 2.5v2",
  check: "M3 8.5 6.2 11.6 13 4.8",
  file: "M5.5 2.5h5l3 3v8h-8zM10.5 2.5v3h3M7.5 8.5v3M9.5 7.5v4M11.5 9v1.5",
  search: "M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z",
  gear: "M8 10.2A2.2 2.2 0 1 0 8 5.8a2.2 2.2 0 0 0 0 4.4ZM8 1.8v1.4M8 12.8v1.4M14.2 8h-1.4M3.2 8H1.8M12.4 3.6l-1 1M4.6 11.4l-1 1M12.4 12.4l-1-1M4.6 4.6l-1-1",
  chat: "M2.5 3.5h11v7.5h-6l-3 2.5V11h-2z",
  send: "M3 8h9M8.5 4.5 12 8l-3.5 3.5",
  record: "M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0ZM9.8 8a1.8 1.8 0 1 1-3.6 0 1.8 1.8 0 0 1 3.6 0Z",
  stop: "M4.5 4.5h7v7h-7z",
  sun: "M8 10.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM8 1.5V3M8 13v1.5M14.5 8H13M3 8H1.5M12.6 3.4l-1 1M4.4 11.6l-1 1M12.6 12.6l-1-1M4.4 4.4l-1-1",
  moon: "M13 9.8A5.5 5.5 0 1 1 6.2 3a4.5 4.5 0 0 0 6.8 6.8Z",
  screen: "M2.5 3.5h11v7.5h-11zM6 13.5h4M8 11v2.5",
  radar: "M9.5 8a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0ZM4.8 11.2a4.5 4.5 0 0 1 0-6.4M11.2 4.8a4.5 4.5 0 0 1 0 6.4M2.7 13.3a7.5 7.5 0 0 1 0-10.6M13.3 2.7a7.5 7.5 0 0 1 0 10.6",
  meeting: "M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0ZM8 5v3.2l2 1.2",
  lines: "M2.5 4h11M4.5 8h7M6.5 12h3",
  play: "M5 3.5v9l7-4.5z",
  down: "M4 6.5 8 10.5l4-4",
  up: "M4 9.5 8 5.5l4 4",
} as const;

type Icon = keyof typeof ICONS;

interface Command {
  id: string;
  group: MessageKey;
  label: string;
  icon: Icon;
  /** Other words for it, matched as well as the label: "theme" finds Dark. */
  aliases?: string[];
  /** The alias that matched, when it matched better than the label did. */
  matched?: string;
  /** A second, quieter line: where a hit came from, or who said it. */
  sub?: string;
  /** A search hit's matched line, with the term marked. */
  snippet?: string;
  hint?: string;
  keys?: string;
  run: () => void;
}

/** How often each command has been chosen, and when — kept per browser. */
interface Frecency {
  [id: string]: { hits: number; last: number };
}

const FRECENCY_KEY = "ma.palette.frecency";

function readFrecency(): Frecency {
  try {
    return JSON.parse(window.localStorage.getItem(FRECENCY_KEY) ?? "{}") as Frecency;
  } catch {
    // A private window, blocked site data, or a corrupt value: the palette works
    // without this, it just stops guessing what you meant.
    return {};
  }
}

function recordUse(id: string): void {
  try {
    const all = readFrecency();
    const seen = all[id] ?? { hits: 0, last: 0 };
    all[id] = { hits: seen.hits + 1, last: Date.now() };
    window.localStorage.setItem(FRECENCY_KEY, JSON.stringify(all));
  } catch {
    /* nothing to do: this is a convenience, never a requirement */
  }
}

/** The order groups are drawn in. A meeting name is a stronger answer than a sentence. */
const GROUP_ORDER: MessageKey[] = [
  "palette.recent",
  "palette.meeting",
  "palette.open",
  "palette.actionItems",
  "palette.summaries",
  "palette.transcript",
  "palette.navigate",
  "palette.do",
];

/**
 * Everything the application can do, and everything it has recorded, in one list —
 * reachable with Ctrl+K or the sidebar's search button.
 *
 * It used to be a command list beside a `/search` screen that shared nothing with it:
 * typing a word from a meeting here found the meeting's *name* or nothing, while the
 * sentence you actually remembered was one screen away. Now the palette searches the
 * same index the Search screen does and groups what it finds — meetings, action
 * items, the lines of transcript — above the commands, with a key-hint footer that
 * stays put. The Search screen is the long form of the same results.
 *
 * It also teaches. Each row shows the key that would have got you there without
 * opening this, which is how Superhuman turns a palette into a tutor.
 *
 * Numbers are from the shipped implementations rather than invented: a 640px root,
 * anchored near the top rather than centred vertically, a list capped around 400px,
 * group headings at 12px, and "No results found." when nothing matches.
 */
export default function CommandPalette() {
  const { t, setTheme, locale } = useI18n();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // On a meeting, its keys are commands too: the palette is where they are learned.
  const onMeeting = pathname.startsWith("/m/");
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const restoreTo = useRef<Element | null>(null);

  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings(), enabled: open });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, enabled: open });
  const term = query.trim();
  const searching = open && term.length > 1;
  const found = useQuery({
    queryKey: ["search", term],
    queryFn: () => api.search(term),
    enabled: searching,
    placeholderData: (previous) => previous,
  });
  const start = useMutation({
    mutationFn: () => api.startRecording(),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const { stop } = useRecordingControls();

  const recording = Boolean(status.data?.recorder.active || status.data?.recorder.paused);

  const detection = status.data?.detector.mode;

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to);
    /** A locale's comma-separated list of other words for a command. */
    const aka = (key: MessageKey) =>
      t(key)
        .split(",")
        .map((word) => word.trim())
        .filter(Boolean);
    const list: Command[] = [];

    if (onMeeting) {
      /*
       * Run a tick later: the palette gives focus back to where it was as it closes,
       * and the find box has to be focused after that, not before.
       */
      const send = (key: MeetingKey) => () => window.setTimeout(() => meetingKey(key), 0);
      list.push(
        {
          id: "meeting.play",
          group: "palette.meeting",
          label: t("palette.playPause"),
          icon: "play",
          aliases: aka("palette.aliasPlay"),
          keys: "Space",
          run: send("play"),
        },
        {
          id: "meeting.next",
          group: "palette.meeting",
          label: t("palette.nextTurn"),
          icon: "down",
          aliases: aka("palette.aliasTurn"),
          keys: "J",
          run: send("next"),
        },
        {
          id: "meeting.previous",
          group: "palette.meeting",
          label: t("palette.previousTurn"),
          icon: "up",
          aliases: aka("palette.aliasTurn"),
          keys: "K",
          run: send("previous"),
        },
        {
          id: "meeting.find",
          group: "palette.meeting",
          label: t("meeting.findInTranscript"),
          icon: "search",
          aliases: aka("palette.aliasFind"),
          keys: "/",
          run: send("find"),
        },
      );
    }

    list.push(
      {
        id: "nav.timeline",
        group: "palette.navigate",
        label: t("nav.timeline"),
        icon: "calendar",
        aliases: aka("palette.aliasTimeline"),
        keys: "G L",
        run: go("/"),
      },
      {
        id: "nav.actions",
        group: "palette.navigate",
        label: t("nav.actions"),
        icon: "check",
        aliases: aka("palette.aliasActions"),
        keys: "G A",
        run: go("/actions"),
      },
      {
        id: "nav.transcriptions",
        group: "palette.navigate",
        label: t("nav.transcriptions"),
        icon: "file",
        aliases: aka("palette.aliasTranscriptions"),
        keys: "G T",
        run: go("/transcriptions"),
      },
      {
        id: "assistant",
        group: "palette.do",
        label: t("assistant.open"),
        icon: "chat",
        aliases: aka("palette.aliasAssistant"),
        keys: "Ctrl J",
        run: () => window.dispatchEvent(new CustomEvent("upshot:assistant")),
      },
      {
        id: "nav.search",
        group: "palette.navigate",
        label: t("nav.search"),
        icon: "search",
        aliases: aka("palette.aliasSearch"),
        // On a meeting, / finds in its transcript instead.
        keys: onMeeting ? "G S" : "/",
        run: go("/search"),
      },
      {
        id: "nav.settings",
        group: "palette.navigate",
        label: t("nav.settings"),
        icon: "gear",
        aliases: aka("palette.aliasSettings"),
        keys: "G ,",
        run: go("/settings"),
      },
      // Anonymous unless an email is added (D87).
      {
        id: "feedback",
        group: "palette.do",
        label: t("feedback.open"),
        icon: "send",
        aliases: aka("palette.aliasFeedback"),
        run: openFeedback,
      },
    );

    list.push(
      recording
        ? {
            id: "rec.stop",
            group: "palette.do",
            label: t("timeline.stop"),
            icon: "stop",
            aliases: aka("palette.aliasStop"),
            run: () => stop.mutate(),
          }
        : {
            id: "rec.start",
            group: "palette.do",
            label: t("timeline.start"),
            icon: "record",
            aliases: aka("palette.aliasRecord"),
            keys: "Ctrl R",
            run: () => start.mutate(),
          },
    );

    /*
     * The detection mode, the one setting someone changes on the way into or out of a
     * call ("not this one") — which is why it is here and the others are not. Saved
     * the way Settings saves it; the mode in force says so.
     */
    for (const mode of DETECTION_MODES) {
      list.push({
        id: `detection.${mode.value}`,
        group: "palette.do",
        label: t(mode.label),
        icon: "radar",
        aliases: aka("palette.aliasDetection"),
        hint: t("settings.detection"),
        sub: detection === mode.value ? t("palette.current") : undefined,
        run: () => {
          void api.putSettings({ "detection.mode": mode.value }).then(() => {
            void queryClient.invalidateQueries({ queryKey: ["settings"] });
            void queryClient.invalidateQueries({ queryKey: ["status"] });
          });
        },
      });
    }

    for (const option of ["light", "dark", "system"] as const) {
      list.push({
        id: `theme.${option}`,
        group: "palette.do",
        icon: option === "light" ? "sun" : option === "dark" ? "moon" : "screen",
        aliases: aka("palette.aliasTheme"),
        label: t(
          option === "light"
            ? "settings.themeLight"
            : option === "dark"
              ? "settings.themeDark"
              : "settings.themeSystem",
        ),
        hint: t("settings.theme"),
        run: () => {
          // Applied before it is saved, so the palette closes onto the new theme
          // rather than onto a round trip.
          applyTheme(option as Theme);
          setTheme(option as Theme);
          void api.putSettings({ "ui.theme": option });
        },
      });
    }

    for (const meeting of meetings.data?.meetings ?? []) {
      list.push({
        id: `meeting.${meeting.id}`,
        group: "palette.open",
        label: meeting.title ?? meeting.id,
        icon: "meeting",
        sub: new Date(meeting.started_at).toLocaleDateString(locale, { day: "numeric", month: "short" }),
        run: go(`/m/${meeting.id}`),
      });
    }
    return list;
  }, [t, navigate, recording, meetings.data, start, stop, setTheme, locale, onMeeting, detection, queryClient]);

  /** Server hits for what was typed: action items and transcript lines. */
  const hits = useMemo<Command[]>(() => {
    if (!searching) return [];
    const byMeeting = new Map((meetings.data?.meetings ?? []).map((m) => [m.id, m]));
    return (found.data?.hits ?? [])
      .filter((hit: SearchHit) => hit.kind !== "title")
      .slice(0, 12)
      .map((hit, index) => {
        const title = hit.meeting_title ?? byMeeting.get(hit.meeting_id)?.title ?? hit.meeting_id;
        return {
          id: `hit.${hit.kind}.${hit.meeting_id}.${hit.at_ms}.${index}`,
          group:
            hit.kind === "action"
              ? "palette.actionItems"
              : hit.kind === "summary"
                ? "palette.summaries"
                : "palette.transcript",
          icon: hit.kind === "action" ? "check" : hit.kind === "summary" ? "lines" : "chat",
          // A summary is a whole document; the row shows the part that matched.
          label: hit.kind === "summary" ? hit.snippet.replace(/[[\]]/g, "") : hit.text,
          snippet: hit.snippet,
          sub:
            hit.kind === "transcript"
              ? `${title} · ${
                  hitSpeaker(hit, t)
                } · ${formatOffset(hit.at_ms / 1000)}`
              : title,
          run: () => {
            rememberSearch(term);
            navigate(hit.kind === "transcript" ? `/m/${hit.meeting_id}?at=${hit.at_ms}` : `/m/${hit.meeting_id}`);
          },
        } satisfies Command;
      });
  }, [searching, found.data, meetings.data, navigate, t, term]);

  const results = useMemo(() => {
    if (!term) {
      // Before anything is typed: the few most recent meetings, then everything to do.
      const recent = commands
        .filter((command) => command.group === "palette.open")
        .slice(0, 5)
        .map((command) => ({ ...command, group: "palette.recent" as MessageKey }));
      return [...recent, ...commands.filter((command) => command.group !== "palette.open")];
    }
    const now = Date.now();
    const frecency = readFrecency();
    const ranked = commands
      .map((command) => {
        const own = score(`${command.label} ${command.hint ?? ""}`.trim(), term);
        // The best of the aliases, kept only when it beats the label, so the row can
        // say which word it was found by: "Dark (theme)".
        let alias: { word: string; rank: number } | null = null;
        for (const word of command.aliases ?? []) {
          const rank = score(word, term);
          if (rank > own && rank > (alias?.rank ?? 0)) alias = { word, rank };
        }
        const base = alias?.rank ?? own;
        if (base === 0) return null;
        const seen = frecency[command.id];
        return {
          command: alias ? { ...command, matched: alias.word } : command,
          rank: base * boost(seen?.hits ?? 0, seen?.last ?? 0, now),
        };
      })
      .filter((row): row is { command: Command; rank: number } => row !== null)
      .sort((a, b) => b.rank - a.rank)
      .slice(0, 40)
      .map((row) => row.command);
    const all = [...ranked, ...hits];
    // Grouped in a fixed order, and the keyboard walks the same order it is drawn in.
    return GROUP_ORDER.flatMap((group) => all.filter((command) => command.group === group));
  }, [commands, hits, term]);

  const groups = useMemo(() => {
    const byGroup = new Map<MessageKey, Command[]>();
    for (const command of results) {
      const bucket = byGroup.get(command.group) ?? [];
      bucket.push(command);
      byGroup.set(command.group, bucket);
    }
    return [...byGroup.entries()];
  }, [results]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // The same key opens and closes it, and closing puts focus back where it
      // was — a palette that swallows your place is worse than no palette.
      // The physical key, as Ctrl+J and Ctrl+R are: on a Hebrew layout event.key is "ל".
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.code === "KeyK") {
        event.preventDefault();
        setOpen((was) => {
          if (!was) restoreTo.current = document.activeElement;
          return !was;
        });
      }
    };
    const onOpen = () => {
      restoreTo.current = document.activeElement;
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("upshot:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("upshot:palette", onOpen);
    };
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      inputRef.current?.focus();
    } else if (restoreTo.current instanceof HTMLElement) {
      restoreTo.current.focus();
    }
  }, [open]);

  useEffect(() => setActive(0), [query]);

  // Keep the highlighted row on screen without scrolling the page behind it.
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  const choose = (command: Command) => {
    recordUse(command.id);
    setOpen(false);
    command.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === "ArrowDown" || (event.ctrlKey && event.code === "KeyN")) {
      event.preventDefault();
      setActive((index) => Math.min(results.length - 1, index + 1));
    } else if (event.key === "ArrowUp" || (event.ctrlKey && event.code === "KeyP")) {
      event.preventDefault();
      setActive((index) => Math.max(0, index - 1));
    } else if (event.key === "Enter" && results[active]) {
      event.preventDefault();
      choose(results[active]);
    }
  };

  let flat = -1;

  return (
    <div
      data-testid="palette-backdrop"
      className="fixed inset-0 z-50 flex items-start justify-center bg-scrim px-4 pt-[14vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setOpen(false);
      }}
    >
      <div
        data-testid="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label={t("palette.title")}
        className="flex w-full max-w-160 flex-col overflow-hidden rounded-xl bg-raised shadow-lg"
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-line-subtle px-5">
          <svg viewBox="0 0 16 16" className="size-4 shrink-0 fill-none stroke-current stroke-[1.5] text-tertiary">
            <path d="M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z" />
          </svg>
          <input
            ref={inputRef}
            data-testid="palette-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("palette.placeholder")}
            aria-label={t("palette.placeholder")}
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={results[active] ? `palette-${active}` : undefined}
            className="no-focus-ring min-w-0 flex-1 bg-transparent py-4 text-lg outline-none placeholder:text-tertiary"
          />
        </div>

        <div
          id="palette-list"
          ref={listRef}
          role="listbox"
          aria-label={t("palette.title")}
          /*
           * Tall enough for a heading and four and a half rows: the fifth is cut on
           * purpose, which says "there is more" without a scrollbar (Superhuman).
           */
          className="max-h-[13.5rem] overflow-y-auto overscroll-contain p-2"
        >
          {results.length === 0 && !(searching && found.isFetching) && (
            <p data-testid="palette-empty" className="grid h-16 place-items-center text-sm text-tertiary">
              {t("palette.empty")}
            </p>
          )}

          {groups.map(([group, items]) => (
            <div key={group} data-testid="palette-group" data-group={group}>
              <p className="px-2 pt-2 pb-1 text-xs text-tertiary">{t(group)}</p>
              {items.map((command) => {
                flat += 1;
                const index = flat;
                return (
                  <button
                    key={command.id}
                    id={`palette-${index}`}
                    data-index={index}
                    data-testid="palette-item"
                    role="option"
                    aria-selected={index === active}
                    type="button"
                    onMouseMove={() => setActive(index)}
                    onClick={() => choose(command)}
                    className={`flex w-full items-center gap-3 rounded-md px-3 text-start text-sm ${
                      command.snippet ? "min-h-11 py-1.5" : "h-10"
                    } ${index === active ? "bg-surface-2 text-primary" : "text-secondary"}`}
                  >
                    <svg
                      viewBox="0 0 16 16"
                      aria-hidden="true"
                      data-testid="palette-icon"
                      data-icon={command.icon}
                      className={`size-4 shrink-0 fill-none stroke-current stroke-[1.5] ${
                        index === active ? "text-secondary" : "text-tertiary"
                      }`}
                    >
                      <path d={ICONS[command.icon]} />
                    </svg>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">
                        {command.snippet
                          ? splitSnippet(command.snippet).map((part, at) =>
                              part.hit ? (
                                <mark key={at} className="rounded-xs bg-accent-quiet px-0.5 text-primary">
                                  {part.text}
                                </mark>
                              ) : (
                                <span key={at}>{part.text}</span>
                              ),
                            )
                          : command.matched
                            ? (
                                <>
                                  {command.label}{" "}
                                  <span data-testid="palette-alias" className="text-tertiary">
                                    ({command.matched})
                                  </span>
                                </>
                              )
                            : command.label}
                      </span>
                      {command.snippet && command.sub && (
                        <span className="block truncate text-2xs text-tertiary">{command.sub}</span>
                      )}
                    </span>
                    {!command.snippet && command.sub && (
                      <span className="shrink-0 text-xs text-tertiary tabular-nums">{command.sub}</span>
                    )}
                    {command.hint && <span className="shrink-0 text-xs text-tertiary">{command.hint}</span>}
                    {/* The binding that would have skipped this palette entirely. */}
                    {command.keys && (
                      <kbd className="kbd-hint shrink-0 rounded-xs bg-surface-3 px-1.5 py-0.5 text-2xs text-tertiary">
                        {command.keys}
                      </kbd>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        {/* The keys that work here, always in the same place — cmdk's footer. */}
        <div
          data-testid="palette-footer"
          className="flex items-center gap-3 border-t border-line-subtle px-4 py-2 text-2xs text-tertiary"
        >
          <span className="kbd-hint flex items-center gap-1">
            <kbd className="rounded-xs bg-surface-3 px-1 py-px">↑↓</kbd> {t("palette.move")}
          </span>
          <span className="kbd-hint flex items-center gap-1">
            <kbd className="rounded-xs bg-surface-3 px-1 py-px">↵</kbd> {t("palette.choose")}
          </span>
          <span className="kbd-hint flex items-center gap-1">
            <kbd className="rounded-xs bg-surface-3 px-1 py-px">{"esc"}</kbd> {t("palette.close")}
          </span>
          {term.length > 1 && (
            <button
              type="button"
              data-testid="palette-all-results"
              onClick={() => {
                rememberSearch(term);
                setOpen(false);
                navigate(`/search?q=${encodeURIComponent(term)}`);
              }}
              className="ms-auto rounded-sm px-1.5 py-0.5 text-secondary hover:bg-a-200 hover:text-primary"
            >
              {t("palette.allResults")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
