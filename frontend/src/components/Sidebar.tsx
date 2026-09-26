import { useRef, useState } from "react";
import { NavLink, useMatch, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRecordingControls } from "../lib/recording";
import { api } from "../api";
import { useI18n } from "../i18n";
import { groupByDay } from "../lib/timeline";
import { formatBytes, formatDayLabel } from "../lib/format";
import { applyTheme } from "../theme";
import { Logo } from "./Logo";
import MeetingCard from "../components/MeetingCard";
import { MenuSurface, type MenuAnchor } from "./Menu";
import Tooltip from "./Tooltip";
import SetupMark from "./SetupMark";
import { useSetupWarnings } from "../lib/setup";
import { confirmDialog } from "./ConfirmDialog";
import { toast } from "./Toaster";
import type { MessageKey } from "../locales/en";

/**
 * The sidebar: navigation and the library, in one 240px column.
 *
 * This replaces a 48px icon rail beside a separate 280px meeting list. Two things
 * were wrong with that. The rail could not carry a label, a count, a section or an
 * account, so five near-identical 16px glyphs had to be memorised — and of the
 * fourteen desktop-class products surveyed for this redesign, *none* ships a bare
 * icon rail; Linear, Attio, Reflect, Cursor and Obsidian all run icon + text at
 * 220-245px. And the two columns together cost 328px of chrome before any content,
 * which is what forced the reading pane into a centred ribbon with 248px of dead
 * gutter on each side.
 *
 * Folding them together costs 88px less, gives every destination a name, and leaves
 * the detail pane wide enough to carry a rail of its own.
 */
const NAV: { to: string; key: MessageKey; hint: MessageKey; testid: string; path: string; end?: boolean }[] = [
  {
    to: "/",
    key: "nav.timeline",
    hint: "help.library",
    testid: "nav-timeline",
    end: true,
    path: "M2.5 4.5h11v9h-11zM2.5 7h11M5.5 2.5v2M10.5 2.5v2",
  },
  { to: "/actions", key: "nav.actions", hint: "help.actions", testid: "nav-actions", path: "M3 8.5 6.2 11.6 13 4.8" },
  {
    to: "/search",
    key: "nav.search",
    hint: "help.search",
    testid: "nav-search",
    path: "M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z",
  },
  {
    to: "/settings",
    key: "nav.settings",
    hint: "help.settings",
    testid: "nav-settings",
    path: "M8 10.2A2.2 2.2 0 1 0 8 5.8a2.2 2.2 0 0 0 0 4.4ZM8 1.8v1.4M8 12.8v1.4M14.2 8h-1.4M3.2 8H1.8M12.4 3.6l-1 1M4.6 11.4l-1 1M12.4 12.4l-1-1M4.6 4.6l-1-1",
  },
];

/** Opens the command palette from anywhere, the way Ctrl+K does. */
export function openPalette(): void {
  window.dispatchEvent(new CustomEvent("upshot:palette"));
}

export default function Sidebar() {
  const { t, locale, theme, setTheme, setLocale } = useI18n();
  const [workspace, setWorkspace] = useState<MenuAnchor | null>(null);
  const brand = useRef<HTMLButtonElement | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const listRef = useRef<HTMLDivElement | null>(null);

  const open = useMatch("/m/:id");
  const openId = open?.params.id;

  const meetings = useQuery({ queryKey: ["meetings"], queryFn: () => api.meetings() });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const openItems = useQuery({
    queryKey: ["action-items", "open"],
    queryFn: () => api.actionItems({ open: "true" }),
  });
  const calendar = useQuery({ queryKey: ["calendar"], queryFn: api.calendarStatus });
  const setup = useSetupWarnings();
  const setupNeeded = [
    setup.calendar ? t("setup.calendarMissing") : null,
    setup.summaries ? t("setup.summariesMissing") : null,
  ].filter((reason): reason is string => reason !== null);

  const { start, stop } = useRecordingControls();
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteMeeting(id),
    onSuccess: (_result, id) => {
      if (id === openId) navigate("/");
      void queryClient.invalidateQueries();
    },
  });
  const resummarize = useMutation({
    mutationFn: (id: string) => api.retry(id, "summarize", true),
    onSuccess: () => {
      toast({ title: t("toast.resummarizing") });
      void queryClient.invalidateQueries();
    },
  });

  /*
   * Deleting asks first, and says what is lost and where it lives. There is no undo
   * — the folder is removed from disk — so the question is the only protection, and
   * it has to name the meeting rather than ask "are you sure?" about nothing.
   */
  const askToDelete = async (id: string, title: string) => {
    const yes = await confirmDialog({
      title: t("meeting.deleteTitle"),
      body: (
        <>
          {t("meeting.deleteBodyBefore")} <b className="font-semibold text-primary">{title}</b>{" "}
          {t("meeting.deleteBodyAfter")}
        </>
      ),
      confirm: t("meeting.deletePermanently"),
      cancel: t("common.cancel"),
    });
    if (!yes) return;
    remove.mutate(id, {
      onSuccess: () => toast({ title: t("toast.deleted").replace("{title}", title) }),
    });
  };

  const switchTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
    void api.putSettings({ "ui.theme": next });
  };
  const switchLanguage = () => {
    const next = locale === "he" ? "en" : "he";
    setLocale(next);
    void api.putSettings({ "ui.language": next });
  };

  const items = meetings.data?.meetings ?? [];
  const byDay = groupByDay(items);
  const recording = status.data?.recorder.active ?? false;
  const queued = status.data?.queue_depth ?? 0;

  /*
   * Keyboard movement through the list. Both j/k and the arrows, unmodified: cmdk
   * ships vim bindings by default, Linear documents "arrow/J-K", Gmail is j and k.
   * Moving the selection replaces the history entry rather than pushing one, or
   * holding j would bury the back button under fifty of them.
   */
  const move = (by: number) => {
    if (items.length === 0) return;
    const at = items.findIndex((meeting) => meeting.id === openId);
    const next = at === -1 ? 0 : Math.min(items.length - 1, Math.max(0, at + by));
    navigate(`/m/${items[next].id}`, { replace: at !== -1 });
  };
  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (event.target instanceof HTMLInputElement) return;
    const key = event.key;
    if (key === "j" || key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (key === "k" || key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (key === "Enter" || key === "o") {
      event.preventDefault();
      document.querySelector<HTMLElement>("[data-detail-pane]")?.focus();
    }
  };

  return (
    <nav
      data-testid="rail"
      aria-label={t("app.title")}
      className="flex w-60 shrink-0 flex-col border-e border-line-subtle bg-surface-1"
    >
      <div className="flex items-center gap-2 px-2.5 pt-2.5 pb-2">
        {/*
         * The brand row is a menu, as it is in Linear, Notion and Attio: the chevron
         * says "this opens", and what it opens is what belongs to the whole
         * application rather than to any one screen.
         */}
        <Tooltip label={t("app.title")} hint={t("help.workspace")}>
        <button
          ref={brand}
          type="button"
          data-testid="app-title"
          aria-haspopup="menu"
          aria-expanded={workspace !== null}
          onClick={() => {
            const rect = brand.current?.getBoundingClientRect();
            if (!rect) return;
            const rtl = document.documentElement.dir === "rtl";
            setWorkspace(workspace ? null : { x: rtl ? rect.right : rect.left, y: rect.bottom + 4, align: "start" });
          }}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-sm px-1.5 py-1 text-start hover:bg-a-200 active:bg-a-300"
        >
          <span className="grid size-5.5 shrink-0 place-items-center rounded-md bg-accent text-on-accent">
            <Logo className="w-3.5" />
          </span>
          <span className="truncate text-sm font-semibold tracking-snug">{t("app.title")}</span>
          <svg
            data-testid="workspace-chevron"
            viewBox="0 0 16 16"
            className="size-3 shrink-0 fill-none stroke-current stroke-[1.5] text-tertiary"
          >
            <path d="M4 6.5 8 10.5l4-4" />
          </svg>
        </button>
        </Tooltip>
        {workspace && (
          <MenuSurface
            testid="workspace-menu"
            at={workspace}
            onClose={(refocus) => {
              setWorkspace(null);
              if (refocus) brand.current?.focus();
            }}
            items={[
              { id: "settings", label: t("nav.settings"), keys: "G ,", run: () => navigate("/settings") },
              {
                id: "theme",
                label: theme === "dark" ? t("workspace.lightTheme") : t("workspace.darkTheme"),
                run: switchTheme,
              },
              {
                id: "language",
                label: locale === "he" ? "English" : "עברית",
                run: switchLanguage,
              },
              { id: "palette", label: t("workspace.commands"), keys: "Ctrl K", separated: true, run: openPalette },
            ]}
          />
        )}
        <Tooltip label={t("nav.search")} keys="Ctrl K">
          <button
            type="button"
            data-testid="nav-search-icon"
            aria-label={t("nav.search")}
            onClick={openPalette}
            className="grid size-7 shrink-0 place-items-center rounded-md text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300"
          >
            <svg viewBox="0 0 16 16" className="size-4 fill-none stroke-current stroke-[1.5]">
              <path d="M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z" />
            </svg>
          </button>
        </Tooltip>
      </div>

      {/*
       * Recording sits at the top, not the foot. Apple's guidance is blunt about the
       * reason: "avoid putting critical information or actions at the bottom of a
       * sidebar. People often relocate a window in a way that hides its bottom edge."
       * It is a labelled button now rather than a bare red circle — the one control
       * that must not be guessed at.
       */}
      {/*
       * One button, two states, in the same place: Record while idle, Stop while a
       * meeting records. It used to grey out while recording, and a greyed-out
       * record button is exactly where someone clicks to stop — to no effect.
       */}
      <Tooltip label={recording ? t("timeline.stop") : t("timeline.start")} keys="Ctrl R" hint={t("help.record")} side="end">
      <button
        type="button"
        data-testid={recording ? "stop-recording" : "start-recording"}
        aria-busy={start.isPending || stop.isPending}
        onClick={() => (recording ? stop.mutate() : start.mutate())}
        className={`mx-2.5 mb-2 flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm font-medium shadow-[var(--shadow-ring),var(--shadow-sm),var(--shadow-edge)] ${
          recording ? "bg-danger text-on-solid hover:brightness-95" : "bg-raised hover:bg-a-200 active:bg-a-300"
        }`}
      >
        <span className={`size-2.5 shrink-0 ${recording ? "rounded-[2px] bg-on-solid" : "rounded-full bg-danger"}`} />
        <span className="truncate">{recording ? t("timeline.stop") : t("timeline.start")}</span>
        <kbd className="ms-auto shrink-0 rounded-[3px] px-1 font-mono text-[10px] text-tertiary shadow-[var(--shadow-ring-subtle)]">
          ⌘R
        </kbd>
      </button>
      </Tooltip>

      <div className="flex flex-col gap-px px-2">
        {NAV.map((item) => (
          <Tooltip key={item.to} label={t(item.key)} hint={t(item.hint)} side="end">
          <NavLink
            to={item.to}
            end={item.end}
            data-testid={item.testid}
            className={({ isActive }) =>
              `flex h-8 items-center gap-2.5 rounded-md px-2 text-sm ${
                isActive
                  ? "bg-raised text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]"
                  : "text-secondary hover:bg-a-200 hover:text-primary active:bg-a-300"
              }`
            }
          >
            <svg viewBox="0 0 16 16" className="size-4 shrink-0 fill-none stroke-current stroke-[1.5]">
              <path d={item.path} />
            </svg>
            <span className="truncate">{t(item.key)}</span>
            {item.to === "/actions" && (openItems.data?.open ?? 0) > 0 && (
              <span className="ms-auto font-mono text-2xs text-tertiary tabular-nums">
                {openItems.data?.open}
              </span>
            )}
            {item.to === "/settings" && setupNeeded.length > 0 && (
              <span className="ms-auto">
                <SetupMark testid="settings-warning" label={setupNeeded.join(" · ")} />
              </span>
            )}
            {item.to === "/search" && (
              <kbd className="ms-auto shrink-0 rounded-[3px] px-1 font-mono text-[10px] text-tertiary shadow-[var(--shadow-ring-subtle)]">
                /
              </kbd>
            )}
          </NavLink>
          </Tooltip>
        ))}
      </div>

      {/*
       * The library, under a section header rather than in a column of its own.
       * The count belongs in the header, which is where Cursor and Linear put it,
       * rather than on a badge beside every row.
       */}
      <div className="flex items-center gap-2 px-4 pt-4 pb-1.5">
        <span
          data-testid="timeline"
          className="text-2xs font-medium uppercase tracking-wide text-tertiary"
        >
          {t("timeline.recent")}
        </span>
        <span className="font-mono text-2xs text-tertiary tabular-nums">{items.length}</span>
        {queued > 0 ? (
          <span
            data-testid="queue-depth"
            title={t("timeline.queued")}
            className="ms-auto shrink-0 whitespace-nowrap rounded-full bg-surface-3 px-1.5 text-2xs text-secondary tabular-nums"
          >
            {queued}
          </span>
        ) : (
          <span className="sr-only" data-testid="queue-depth">
            {t("timeline.queued")}: 0
          </span>
        )}
      </div>

      <div
        ref={listRef}
        data-testid="meeting-list"
        role="listbox"
        aria-label={t("nav.timeline")}
        tabIndex={0}
        onKeyDown={onListKeyDown}
        className="ma-list min-h-0 flex-1 overflow-y-auto px-2 pb-20 outline-none"
      >
        {meetings.isLoading && (
          <p data-testid="loading" className="px-2 py-3 text-sm text-tertiary">
            {t("common.loading")}
          </p>
        )}
        {meetings.isError && (
          <p data-testid="error" className="px-2 py-3 text-sm text-danger">
            {t("common.error")}
          </p>
        )}
        {!meetings.isLoading && items.length === 0 && (
          <p data-testid="timeline-empty" className="px-2 py-10 text-center text-sm text-tertiary">
            {t("timeline.empty")}
          </p>
        )}
        {byDay.map(([day, group]) => (
          <div key={day} data-testid="timeline-day" data-day={day} className="mb-1">
            <h2 className="px-2 pt-2.5 pb-1 text-2xs uppercase tracking-wide text-tertiary">
              {formatDayLabel(day, locale, t)}
            </h2>
            {group.map((meeting) => (
              <MeetingCard
                key={meeting.id}
                meeting={meeting}
                selected={meeting.id === openId}
                onStop={() => stop.mutate()}
                onResummarize={() => resummarize.mutate(meeting.id)}
                onDelete={() => void askToDelete(meeting.id, meeting.title ?? meeting.id)}
              />
            ))}
          </div>
        ))}
      </div>

      {/*
       * What a local application can say that a hosted one cannot. It is the whole
       * product claim, and it was nowhere in the interface.
       */}
      <div
        data-testid="sidebar-status"
        className="flex items-center gap-1.5 border-t border-line-subtle px-4 py-2.5 text-2xs text-tertiary"
      >
        <span className="size-1.5 rounded-full bg-success" />
        <span className="truncate">
          {t("sidebar.localOnly")}
          {/* How much is on this disk: the other half of "local only". */}
          {formatBytes(status.data?.storage_bytes) && (
            <Tooltip label={formatBytes(status.data?.storage_bytes) ?? ""} hint={t("help.storage")} side="top">
              <span data-testid="sidebar-storage"> · {formatBytes(status.data?.storage_bytes)}</span>
            </Tooltip>
          )}
          {calendar.data?.state === "connected" && ` · ${t("sidebar.calendarConnected")}`}
        </span>
      </div>
    </nav>
  );
}
