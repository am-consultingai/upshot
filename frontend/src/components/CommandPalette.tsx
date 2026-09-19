import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { boost, score } from "../lib/score";
import { applyTheme, type Theme } from "../theme";
import type { MessageKey } from "../locales/en";

interface Command {
  id: string;
  group: MessageKey;
  label: string;
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

/**
 * Everything the application can do, in one list, reachable with Ctrl+K.
 *
 * Every comparable product ships one — Linear, Raycast, Reflect, Obsidian,
 * Todoist and the late Height all bind Cmd/Ctrl+K — and none of them ships a
 * right-click menu to speak of. It is the answer to "where is that setting",
 * "open that meeting" and "what is this app called again" all at once.
 *
 * It also teaches. Each row shows the key that would have got you there without
 * opening this, which is how Superhuman turns a palette into a tutor: you find a
 * thing by typing its name twenty times and learn its shortcut on the way.
 *
 * Numbers are from the shipped implementations rather than invented: a 640px
 * root, anchored near the top rather than centred vertically, a list capped
 * around 400px, group headings at 12px, and "No results found." when nothing
 * matches — which is the literal copy in every one of cmdk's three reference
 * palettes.
 */
export default function CommandPalette() {
  const { t, setTheme } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const restoreTo = useRef<Element | null>(null);

  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings(), enabled: open });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, enabled: open });
  const start = useMutation({
    mutationFn: api.startRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const recording = status.data?.recorder.active ?? false;

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to);
    const list: Command[] = [
      {
        id: "nav.timeline",
        group: "palette.navigate",
        label: t("nav.timeline"),
        keys: "G T",
        run: go("/"),
      },
      { id: "nav.search", group: "palette.navigate", label: t("nav.search"), keys: "G S", run: go("/search") },
      { id: "nav.detector", group: "palette.navigate", label: t("nav.detector"), keys: "G D", run: go("/detector") },
      { id: "nav.settings", group: "palette.navigate", label: t("nav.settings"), keys: "G ,", run: go("/settings") },
    ];

    list.push(
      recording
        ? { id: "rec.stop", group: "palette.do", label: t("timeline.stop"), run: () => stop.mutate() }
        : { id: "rec.start", group: "palette.do", label: t("timeline.start"), run: () => start.mutate() },
    );

    for (const option of ["light", "dark", "system"] as const) {
      list.push({
        id: `theme.${option}`,
        group: "palette.do",
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
        run: go(`/m/${meeting.id}`),
      });
    }
    return list;
  }, [t, navigate, recording, meetings.data, start, stop, setTheme]);

  const results = useMemo(() => {
    const now = Date.now();
    const frecency = readFrecency();
    return commands
      .map((command) => {
        const base = score(`${command.label} ${command.hint ?? ""}`.trim(), query);
        if (base === 0) return null;
        const seen = frecency[command.id];
        return { command, rank: base * boost(seen?.hits ?? 0, seen?.last ?? 0, now) };
      })
      .filter((row): row is { command: Command; rank: number } => row !== null)
      .sort((a, b) => b.rank - a.rank)
      .slice(0, 40)
      .map((row) => row.command);
  }, [commands, query]);

  // Grouped for display, but the keyboard walks the flat list: a group is a label,
  // not a level of navigation.
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
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((was) => {
          if (!was) restoreTo.current = document.activeElement;
          return !was;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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
    } else if (event.key === "ArrowDown" || (event.ctrlKey && event.key === "n")) {
      event.preventDefault();
      setActive((index) => Math.min(results.length - 1, index + 1));
    } else if (event.key === "ArrowUp" || (event.ctrlKey && event.key === "p")) {
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
        className="w-full max-w-[640px] overflow-hidden rounded-xl bg-raised shadow-lg"
        onKeyDown={onKeyDown}
      >
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
          className="no-focus-ring w-full border-b border-line-subtle bg-transparent px-5 py-4 text-lg placeholder:text-tertiary"
        />

        <div
          id="palette-list"
          ref={listRef}
          role="listbox"
          aria-label={t("palette.title")}
          className="max-h-[400px] overflow-y-auto overscroll-contain p-2"
        >
          {results.length === 0 && (
            <p data-testid="palette-empty" className="grid h-16 place-items-center text-sm text-tertiary">
              {t("palette.empty")}
            </p>
          )}

          {groups.map(([group, items]) => (
            <div key={group}>
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
                    className={`flex h-11 w-full items-center gap-3 rounded-md px-3 text-start text-sm ${
                      index === active ? "bg-surface-2 text-primary" : "text-secondary"
                    }`}
                  >
                    <span className="min-w-0 flex-1 truncate">{command.label}</span>
                    {command.hint && <span className="shrink-0 text-xs text-tertiary">{command.hint}</span>}
                    {/* The binding that would have skipped this palette entirely. */}
                    {command.keys && (
                      <kbd className="shrink-0 rounded-xs bg-surface-3 px-1.5 py-0.5 text-2xs text-tertiary">
                        {command.keys}
                      </kbd>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
