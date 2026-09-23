import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type ActionItem } from "../api";
import { useI18n } from "../i18n";
import ActionItemRow, { useActionPatch } from "../components/ActionItemRow";
import { toast } from "../components/Toaster";
import Tooltip from "../components/Tooltip";
import { addDays, dayKey, startOfDay } from "../lib/calendar";
import { dueBucket, isSnoozed, type DueBucket } from "../lib/due";
import { formatShortDate } from "../lib/format";
import { initials, personColour } from "../lib/speakers";
import type { MessageKey } from "../locales/en";

type Tab = "mine" | "everyone" | "done";

const BUCKETS: { id: DueBucket; key: MessageKey; icon: string; tone: string }[] = [
  { id: "overdue", key: "actions.groupOverdue", icon: "M8 4.5v4M8 11v.5M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0Z", tone: "text-danger" },
  { id: "week", key: "actions.groupWeek", icon: "M2.5 3.5h11v10h-11zM2.5 6.5h11M5.5 2v2M10.5 2v2", tone: "text-accent" },
  { id: "later", key: "actions.groupLater", icon: "M2.5 3.5h11v10h-11zM2.5 6.5h11M6.5 9.5h3M8 8v3", tone: "text-secondary" },
  { id: "none", key: "actions.groupNone", icon: "M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0Z", tone: "text-tertiary" },
];

/** Typing into a field is not a shortcut. */
function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    // The sidebar's list has its own J and K.
    (target instanceof HTMLElement && target.closest("[role=listbox]") !== null)
  );
}

/**
 * Everything anyone took on, across every meeting.
 *
 * The screen this application was missing: a meeting-notes tool that files seven
 * well-written documents and cannot answer "what did I promise this week, and to
 * whom" is a filing cabinet.
 *
 * Grouped by when, now that there is a when to group by. `due_at` is a calendar date
 * the server resolves from what was said, against the meeting's own date, in English
 * and Hebrew — the thing the free-text `due` could never be. Overdue first, then this
 * week, then later, then undated; a group with nothing in it is not drawn (Things 3's
 * rule), so the screen is only ever as long as what is owed.
 *
 * Mine / Everyone / Done is the owner question asked as a tab rather than as a
 * grouping: mine is the list I can act on, everyone is who I am waiting for, and done
 * is the record. J and K move, X ticks, H snoozes until tomorrow — shown in the bar,
 * because a shortcut nobody is told about is a shortcut nobody uses.
 */
export default function ActionsPage() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const patch = useActionPatch();
  const [tab, setTab] = useState<Tab>("mine");
  const [selected, setSelected] = useState(0);
  const [filter, setFilter] = useState<string | null>(null);
  const [showSnoozed, setShowSnoozed] = useState(false);
  const filterRef = useRef<HTMLInputElement | null>(null);

  const query = useQuery({ queryKey: ["action-items", "all"], queryFn: () => api.actionItems() });
  const all = query.data?.items ?? [];
  const today = new Date();

  const open = all.filter((item) => !item.done);
  const awake = open.filter((item) => !isSnoozed(item, today));
  const snoozed = open.filter((item) => isSnoozed(item, today));
  const counts = {
    mine: awake.filter((item) => item.mine).length,
    everyone: awake.length,
    done: all.filter((item) => item.done).length,
  };

  const needle = (filter ?? "").trim().toLowerCase();
  const matches = (item: ActionItem) =>
    !needle ||
    [item.what, item.detail ?? "", item.who, item.meeting_title ?? ""].some((text) =>
      text.toLowerCase().includes(needle),
    );

  const shown =
    tab === "done"
      ? all
          .filter((item) => item.done)
          .sort((a, b) => (b.done_at ?? "").localeCompare(a.done_at ?? ""))
      : awake.filter((item) => tab === "everyone" || item.mine);
  const visible = shown.filter(matches);

  const groups: [DueBucket, ActionItem[]][] =
    tab === "done"
      ? []
      : BUCKETS.map(
      (bucket) =>
        [
          bucket.id,
          visible
            .filter((item) => dueBucket(item, today) === bucket.id)
            .sort((a, b) => (a.due_at ?? "").localeCompare(b.due_at ?? "")),
        ] as [DueBucket, ActionItem[]],
        ).filter(([, rows]) => rows.length > 0);

  // The order J and K walk: the order the rows are drawn in.
  const order = tab === "done" ? visible : groups.flatMap(([, rows]) => rows);
  const current = order[Math.min(selected, order.length - 1)];

  useEffect(() => setSelected(0), [tab, needle]);

  // Arrived from the calendar rail's "2 overdue": land on that group.
  useEffect(() => {
    const bucket = location.hash.slice(1);
    if (!bucket || query.isLoading) return;
    if (tab !== "everyone" && !groups.some(([id]) => id === bucket)) setTab("everyone");
    window.setTimeout(
      () => document.querySelector(`[data-bucket-group="${bucket}"]`)?.scrollIntoView({ block: "start" }),
      50,
    );
    // Once per arrival: re-running on every tab change would undo the reader's choice.
  }, [location.hash, query.isLoading]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setFilter((was) => was ?? "");
        window.setTimeout(() => filterRef.current?.focus(), 0);
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return;
      if (document.querySelector("[role=dialog],[role=alertdialog],[role=menu]")) return;
      const key = event.key.toLowerCase();
      if (key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setSelected((at) => Math.min(order.length - 1, at + 1));
      } else if (key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setSelected((at) => Math.max(0, at - 1));
      } else if (key === "x" && current) {
        event.preventDefault();
        patch.mutate({ item: current, patch: { done: !current.done } });
      } else if (key === "h" && current && !current.done) {
        event.preventDefault();
        const until = dayKey(addDays(startOfDay(new Date()), 1));
        const item = current;
        patch.mutate({ item, patch: { snoozed_until: until } });
        toast({
          title: t("actions.snoozedUntil").replace("{when}", t("actions.dueTomorrow")),
          sub: item.what,
          action: { label: t("common.undo"), run: () => patch.mutate({ item, patch: { snoozed_until: null } }) },
        });
      } else if (event.key === "Enter" && current) {
        event.preventDefault();
        navigate(`/m/${current.meeting_id}`);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [order, current, patch, navigate, t]);

  useEffect(() => {
    document
      .querySelector(`[data-testid=action-item][data-action-id="${current?.id}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [current?.id]);

  const row = (item: ActionItem) => (
    <ActionItemRow
      key={item.id}
      item={item}
      selected={item.id === current?.id}
      onFocusRow={() => setSelected(order.findIndex((other) => other.id === item.id))}
    />
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col" data-testid="actions-page">
      <div className="flex h-11 flex-none items-center gap-2 border-b border-line-subtle px-5">
        <span className="text-sm text-secondary">{t("actions.title")}</span>
        <span data-testid="actions-keys" className="ms-auto flex items-center gap-3 text-2xs text-tertiary">
          {(
            [
              ["J K", "actions.keyMove"],
              ["X", "actions.keyDone"],
              ["H", "actions.keySnooze"],
              ["Ctrl F", "actions.keyFilter"],
            ] as const
          ).map(([keys, label]) => (
            <span key={keys} className="flex items-center gap-1">
              <kbd className="rounded-[3px] px-1 font-mono text-[10px] shadow-[var(--shadow-ring-subtle)]">{keys}</kbd>
              {t(label)}
            </span>
          ))}
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto">
          <div className="max-w-[46rem] px-8 pt-7 pb-16">
            <h1 className="text-2xl font-semibold tracking-tight">{t("actions.title")}</h1>
            <p className="mt-1 mb-5 text-md text-tertiary">{t("actions.lead")}</p>

            <div data-testid="actions-tabs" role="tablist" className="mb-6 flex w-max gap-0.5 rounded-md bg-surface-3 p-0.5">
              {(["mine", "everyone", "done"] as const).map((value) => (
                <Tooltip
                  key={value}
                  label={t(value === "mine" ? "actions.tabMine" : value === "everyone" ? "actions.tabEveryone" : "actions.tabDone")}
                  hint={t(value === "mine" ? "help.tabMine" : value === "everyone" ? "help.tabEveryone" : "help.tabDone")}
                >
                <button
                  type="button"
                  role="tab"
                  data-testid={`actions-tab-${value}`}
                  aria-selected={tab === value}
                  aria-pressed={tab === value}
                  onClick={() => setTab(value)}
                  className={`flex h-6.5 items-center gap-1.5 rounded-sm px-3 text-sm ${
                    tab === value
                      ? "bg-raised text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]"
                      : "text-secondary hover:bg-a-200 hover:text-primary"
                  }`}
                >
                  {t(value === "mine" ? "actions.tabMine" : value === "everyone" ? "actions.tabEveryone" : "actions.tabDone")}
                  <span data-testid={`actions-count-${value}`} className="font-mono text-2xs text-tertiary tabular-nums">
                    {counts[value]}
                  </span>
                </button>
                </Tooltip>
              ))}
            </div>

            {filter !== null && (
              <div className="mb-5 flex h-8 items-center gap-2 rounded-md bg-surface-1 px-2.5 shadow-[var(--shadow-ring-subtle)] focus-within:shadow-[0_0_0_1px_var(--accent)]">
                <svg viewBox="0 0 16 16" className="size-3.5 shrink-0 fill-none stroke-current stroke-[1.6] text-tertiary">
                  <path d="M2.5 4h11M4.5 8h7M6.5 12h3" />
                </svg>
                <input
                  ref={filterRef}
                  data-testid="actions-filter"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setFilter(null);
                  }}
                  placeholder={t("actions.filterPlaceholder")}
                  className="no-focus-ring min-w-0 flex-1 bg-transparent text-sm outline-none"
                />
              </div>
            )}

            {query.isLoading && <p className="text-sm text-tertiary">{t("common.loading")}</p>}
            {query.isError && <p className="text-sm text-danger">{t("common.error")}</p>}

            {!query.isLoading && !query.isError && visible.length === 0 && (
              <p data-testid="actions-empty" className="py-10 text-center text-sm text-tertiary">
                {all.length === 0
                  ? t("actions.empty")
                  : tab === "done"
                    ? t("actions.emptyDone")
                    : t("actions.emptyOpen")}
              </p>
            )}

            {groups.map(([bucket, rows]) => {
              const meta = BUCKETS.find((entry) => entry.id === bucket);
              return (
                <section key={bucket} data-testid="action-group" data-bucket-group={bucket} className="mb-7">
                  {/* Things 3's header: a small coloured glyph, a neutral label, a count,
                      and a hairline. Not uppercase, not a tinted band, not sticky. */}
                  <h2 className="flex items-center gap-2 border-b border-line-subtle pb-2 text-sm font-semibold">
                    <svg viewBox="0 0 16 16" className={`size-3.5 fill-none stroke-current stroke-[1.6] ${meta?.tone ?? ""}`} strokeDasharray={bucket === "none" ? "2 2" : undefined}>
                      <path d={meta?.icon ?? ""} />
                    </svg>
                    {meta ? t(meta.key) : bucket}
                    <span className="font-mono text-2xs font-normal text-tertiary tabular-nums">{rows.length}</span>
                    {bucket === "overdue" && (
                      <span className="ms-auto rounded-full bg-danger-quiet px-1.5 py-px font-mono text-2xs font-normal text-danger">
                        {t("actions.lateCount").replace("{n}", String(rows.length))}
                      </span>
                    )}
                  </h2>
                  <ul className="divide-y divide-line-subtle">{rows.map(row)}</ul>
                </section>
              );
            })}

            {tab === "done" && visible.length > 0 && (
              <section data-testid="action-group" data-bucket-group="done" className="mb-7">
                <ul className="divide-y divide-line-subtle">{visible.map(row)}</ul>
              </section>
            )}

            {tab !== "done" && snoozed.length > 0 && (
              <section data-testid="actions-snoozed" className="mb-7">
                <button
                  type="button"
                  onClick={() => setShowSnoozed((was) => !was)}
                  aria-expanded={showSnoozed}
                  className="flex w-full items-center gap-2 border-b border-line-subtle pb-2 text-start text-sm font-semibold text-tertiary hover:text-primary"
                >
                  <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.6]">
                    <path d="M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0ZM8 5v3.2l2 1.2" />
                  </svg>
                  {t("actions.groupSnoozed")}
                  <span className="font-mono text-2xs font-normal tabular-nums">{snoozed.length}</span>
                </button>
                {showSnoozed && (
                  <ul className="divide-y divide-line-subtle opacity-75">
                    {snoozed.map((item) => (
                      <li key={item.id} className="flex items-center gap-2 px-2.5 py-2 text-sm">
                        <span className="min-w-0 flex-1 truncate">{item.what}</span>
                        <span className="font-mono text-2xs text-tertiary">
                          {t("actions.until")} {formatShortDate(`${item.snoozed_until}T00:00:00`, locale)}
                        </span>
                        <button
                          type="button"
                          data-testid="action-unsnooze"
                          onClick={() => patch.mutate({ item, patch: { snoozed_until: null } })}
                          className="rounded-sm px-1.5 text-xs text-secondary hover:bg-a-200 hover:text-primary"
                        >
                          {t("actions.wake")}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </div>
        </div>

        <InboxRail items={all} />
      </div>
    </div>
  );
}

/**
 * The inbox's rail: where these came from, who you are waiting on, and how much
 * got cleared this week — the one number on this screen that is a result rather
 * than a list, set at the 40px display step.
 */
function InboxRail({ items }: { items: ActionItem[] }) {
  const { t, locale } = useI18n();
  const today = new Date();
  const open = items.filter((item) => !item.done && !isSnoozed(item, today));

  const byMeeting = new Map<string, { title: string; started: string | null; open: number }>();
  for (const item of open) {
    const entry = byMeeting.get(item.meeting_id) ?? {
      title: item.meeting_title ?? item.meeting_id,
      started: item.meeting_started_at,
      open: 0,
    };
    entry.open += 1;
    byMeeting.set(item.meeting_id, entry);
  }
  const sources = [...byMeeting.entries()].sort((a, b) => b[1].open - a[1].open).slice(0, 6);

  const waiting = new Map<string, number>();
  for (const item of open.filter((row) => !row.mine)) waiting.set(item.who, (waiting.get(item.who) ?? 0) + 1);
  const people = [...waiting.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);

  const weekAgo = addDays(startOfDay(today), -6).getTime();
  const cleared = items.filter((item) => item.done && item.done_at && new Date(item.done_at).getTime() >= weekAgo).length;
  const outOf = cleared + items.filter((item) => !item.done).length;

  return (
    <aside
      data-testid="actions-rail"
      className="hidden w-[19rem] shrink-0 flex-col gap-6.5 overflow-y-auto border-s border-line-subtle px-5 pt-6.5 pb-10 xl:flex"
    >
      {sources.length > 0 && (
        <section data-testid="actions-sources">
          <h2 className="text-2xs uppercase tracking-wide text-tertiary">{t("actions.railSources")}</h2>
          <ul className="mt-2 grid gap-px">
            {sources.map(([id, entry]) => (
              <li key={id}>
                <Link to={`/m/${id}`} className="-mx-2 block rounded-md px-2 py-1.5 hover:bg-a-200 active:bg-a-300">
                  <span className="block truncate text-sm">{entry.title}</span>
                  <span className="mt-px block font-mono text-2xs text-tertiary tabular-nums">
                    {entry.started && formatShortDate(entry.started, locale)} · {t("actions.nOpen").replace("{n}", String(entry.open))}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {people.length > 0 && (
        <section data-testid="actions-waiting">
          <Tooltip label={t("actions.railWaiting")} hint={t("help.waiting")}>
            <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("actions.railWaiting")}</h2>
          </Tooltip>
          <ul className="mt-2.5 grid gap-2.5">
            {people.map(([who, count]) => (
              <li key={who} data-testid="actions-waiting-person" className="flex items-center gap-2 text-sm">
                <span
                  aria-hidden="true"
                  className="grid size-5.5 shrink-0 place-items-center rounded-full text-[9px] font-semibold text-on-speaker"
                  style={{ background: personColour(who) }}
                >
                  {initials(who)}
                </span>
                <bdi className="min-w-0 flex-1 truncate">{who}</bdi>
                <span className="font-mono text-2xs text-tertiary tabular-nums">{count}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section data-testid="actions-cleared">
        <Tooltip label={t("actions.railCleared")} hint={t("help.cleared")}>
          <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("actions.railCleared")}</h2>
        </Tooltip>
        <p className="mt-2 flex items-baseline gap-2">
          <span data-testid="actions-cleared-count" className="text-4xl leading-none font-[450] tracking-tight tabular-nums">
            {cleared}
          </span>
          <span className="text-sm text-tertiary">{t("actions.ofItems").replace("{n}", String(outOf))}</span>
        </p>
        <div className="mt-3 h-[3px] overflow-hidden rounded-sm bg-surface-3">
          <i className="block h-full rounded-sm bg-accent opacity-60" style={{ width: `${outOf ? (cleared / outOf) * 100 : 0}%` }} />
        </div>
      </section>
    </aside>
  );
}

/** The bucket every spelling of "me" collapses into; the server decides which those are. */
export const MINE = "\u0000me";

/**
 * Owner → their items, mine first and the rest alphabetical.
 *
 * Still exported: "whose is this" is the second question anyone asks of this screen,
 * and the Everyone tab's waiting-on list is built the same way.
 *
 * Mine is sorted by an explicit test rather than by a sentinel key, because
 * `localeCompare` collates on letters and quietly ignores a leading control
 * character: "\u0000me" sorts exactly where "me" would.
 */
export function groupByOwner(items: ActionItem[]): [string, ActionItem[]][] {
  const byOwner = new Map<string, ActionItem[]>();
  for (const item of items) {
    const key = item.mine ? MINE : item.who;
    const bucket = byOwner.get(key);
    if (bucket) bucket.push(item);
    else byOwner.set(key, [item]);
  }
  return [...byOwner.entries()].sort(([a], [b]) => {
    if (a === MINE) return -1;
    if (b === MINE) return 1;
    return a.localeCompare(b);
  });
}
