import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ActionItem, type MeetingDetail } from "../api";
import { useI18n } from "../i18n";
import { addDays, dayKey } from "../lib/calendar";
import { daysUntil, dueLabel, nextWeekday } from "../lib/due";
import { formatShortDate } from "../lib/format";
import { initials, personColour } from "../lib/speakers";
import DatePicker from "./DatePicker";
import Menu, { useContextMenu, type MenuItem } from "./Menu";
import Tooltip from "./Tooltip";
import { toast } from "./Toaster";
import { confirmDialog } from "./ConfirmDialog";

type Patch = Parameters<typeof api.patchActionItem>[1];

/**
 * Change an action item everywhere it is on screen at once, then confirm with the
 * server.
 *
 * Optimistic, because a checkbox that waits for a round trip before it moves does not
 * read as a checkbox. The inbox and the meeting page read the same rows from two
 * different queries, so both are patched; the invalidation that follows corrects
 * either if the write lost.
 */
export function useActionPatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ item, patch }: { item: ActionItem; patch: Patch }) => api.patchActionItem(item.id, patch),
    onMutate: async ({ item, patch }) => {
      const meetingKey = { queryKey: ["meeting", item.meeting_id] };
      await queryClient.cancelQueries({ queryKey: ["action-items"] });
      await queryClient.cancelQueries(meetingKey);
      const previous = [
        ...queryClient.getQueriesData({ queryKey: ["action-items"] }),
        ...queryClient.getQueriesData(meetingKey),
      ];
      const apply = (row: ActionItem) => (row.id === item.id ? { ...row, ...patch } : row);
      const opened = patch.done === undefined ? 0 : patch.done === item.done ? 0 : patch.done ? -1 : 1;
      queryClient.setQueriesData(
        { queryKey: ["action-items"] },
        (old: { items: ActionItem[]; count: number; open: number } | undefined) =>
          old && { ...old, items: old.items.map(apply), open: old.open + opened },
      );
      queryClient.setQueriesData(
        meetingKey,
        (old: MeetingDetail | undefined) =>
          old && {
            ...old,
            action_items: (old.action_items ?? []).map(apply),
            actions_open: Math.max(0, (old.actions_open ?? 0) + opened),
          },
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      for (const [key, value] of context?.previous ?? []) queryClient.setQueryData(key, value);
    },
    onSettled: (_data, _error, { item }) => {
      void queryClient.invalidateQueries({ queryKey: ["action-items"] });
      void queryClient.invalidateQueries({ queryKey: ["meeting", item.meeting_id] });
      // The sidebar counts what each meeting still owes, so ticking the last one off
      // is what turns that row to "done".
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
    },
  });
}

/**
 * One commitment, ticked in place.
 *
 * Two lines, as the mock draws it: what is owed, and beneath it, lighter, why — the
 * detail the summary gave, and in the inbox the meeting it came from. At the end, the
 * due date as a chip (red and counted in days once it is late, a dashed "+ Add date"
 * when there is none) and the owner's initials.
 *
 * Snooze and `⋯` are revealed into a reserved slot on hover or focus, never inserted
 * into the flow; the same actions are on right-click.
 *
 * The tick is the user's and the text is the model's, which is why the two are stored
 * apart: re-summarizing rewrites every row and carries the done state across by the
 * text it matched.
 */
export default function ActionItemRow({
  item,
  showMeeting = true,
  selected = false,
  onFocusRow,
}: {
  item: ActionItem;
  /** Off on the meeting page, where the meeting is the page you are already on. */
  showMeeting?: boolean;
  /** The inbox's keyboard selection. */
  selected?: boolean;
  onFocusRow?: () => void;
}) {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const patch = useActionPatch();
  const [picking, setPicking] = useState<HTMLElement | null>(null);
  const dueRef = useRef<HTMLButtonElement | null>(null);

  const remove = useMutation({
    mutationFn: () => api.deleteActionItem(item.id),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["action-items"] });
      void queryClient.invalidateQueries({ queryKey: ["meeting", item.meeting_id] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
    },
  });

  const snooze = (until: string, label: string) => {
    patch.mutate({ item, patch: { snoozed_until: until } });
    toast({
      title: t("actions.snoozedUntil").replace("{when}", label),
      sub: item.what,
      action: { label: t("common.undo"), run: () => patch.mutate({ item, patch: { snoozed_until: null } }) },
    });
  };
  const tomorrow = dayKey(addDays(new Date(), 1));
  const monday = nextWeekday(1);

  const words = {
    late: (days: number) => t(days === 1 ? "actions.dayLate" : "actions.daysLate").replace("{n}", String(days)),
    today: t("actions.dueToday"),
    tomorrow: t("actions.dueTomorrow"),
  };
  const late = item.due_at !== null && !item.done && daysUntil(item.due_at) < 0;
  const owner = item.mine ? t("meeting.you") : item.who;

  const items: MenuItem[] = [
    {
      id: "done",
      label: item.done ? t("actions.markOpen") : t("actions.markDone"),
      keys: "X",
      run: () => patch.mutate({ item, patch: { done: !item.done } }),
    },
    {
      id: "due",
      label: item.due_at ? t("actions.changeDate") : t("actions.addDate"),
      run: () => setPicking(dueRef.current),
    },
    ...(!item.done
      ? [
          { id: "snooze-tomorrow", label: t("actions.snoozeTomorrow"), keys: "H", run: () => snooze(tomorrow, t("actions.dueTomorrow")) },
          {
            id: "snooze-monday",
            label: t("actions.snoozeMonday"),
            run: () => snooze(monday, formatShortDate(new Date(`${monday}T00:00:00`), locale)),
          },
        ]
      : []),
    ...(item.at_ms !== null
      ? [{ id: "moment", label: t("actions.jumpToMoment"), run: () => navigate(`/m/${item.meeting_id}?at=${item.at_ms}`) }]
      : []),
    ...(showMeeting
      ? [{ id: "meeting", label: t("actions.openMeeting"), run: () => navigate(`/m/${item.meeting_id}`) }]
      : []),
    {
      id: "delete",
      label: t("actions.delete"),
      danger: true,
      separated: true,
      run: () => {
        void confirmDialog({
          title: t("actions.deleteTitle"),
          body: item.what,
          confirm: t("actions.delete"),
          cancel: t("common.cancel"),
        }).then((yes) => {
          if (yes) remove.mutate();
        });
      },
    },
  ];
  const context = useContextMenu(items);

  const when = item.meeting_started_at ? formatShortDate(item.meeting_started_at, locale) : null;

  return (
    <li
      data-testid="action-item"
      data-action-id={item.id}
      data-done={item.done ? "true" : "false"}
      data-mine={item.mine ? "true" : "false"}
      data-due-at={item.due_at ?? undefined}
      aria-selected={showMeeting ? selected : undefined}
      onContextMenu={context.onContextMenu}
      onFocus={onFocusRow}
      className={`ma-row group relative flex items-start gap-2.5 rounded-md px-2.5 ${
        showMeeting ? "py-2.5" : "py-2"
      } ${selected ? "bg-a-100" : "hover:bg-a-200"}`}
    >
      <span className="relative mt-0.5 grid size-[18px] shrink-0 place-items-center">
        <input
          type="checkbox"
          data-testid="action-toggle"
          checked={item.done}
          aria-label={item.done ? t("actions.markOpen") : t("actions.markDone")}
          title={item.done ? t("actions.markOpen") : t("actions.markDone")}
          onChange={(event) => patch.mutate({ item, patch: { done: event.target.checked } })}
          className="peer absolute inset-0 cursor-pointer opacity-0"
        />
        <span
          aria-hidden="true"
          className={`grid size-[18px] place-items-center rounded-full transition-all ${
            item.done
              ? "bg-accent text-on-accent"
              : "text-transparent shadow-[inset_0_0_0_1.5px_var(--border-strong)] peer-hover:shadow-[inset_0_0_0_1.5px_var(--text-tertiary)]"
          } peer-focus-visible:shadow-[0_0_0_2px_var(--surface-0),0_0_0_4px_var(--accent)]`}
        >
          <svg viewBox="0 0 16 16" className="size-2.5 fill-none stroke-current stroke-[2.6]" strokeLinecap="round">
            <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
          </svg>
        </span>
      </span>

      <div className="min-w-0 flex-1">
        <span
          data-testid="action-what"
          className={`block text-md leading-snug ${
            item.done ? "text-tertiary line-through" : showMeeting ? "text-primary" : "font-medium text-primary"
          }`}
        >
          {item.what}
        </span>
        {(showMeeting || item.detail) && (
          <span data-testid="action-provenance" className="mt-0.5 block text-sm text-tertiary">
            {showMeeting && (
              <>
                {t("actions.from")}{" "}
                <Link
                  data-testid="action-meeting"
                  to={`/m/${item.meeting_id}`}
                  className="text-secondary hover:text-primary hover:underline"
                >
                  {item.meeting_title ?? item.meeting_id}
                </Link>
                {when && <span className="tabular-nums">, {when}</span>}
                {item.detail && " — "}
              </>
            )}
            {item.detail && <span data-testid="action-detail">{item.detail}</span>}
          </span>
        )}
      </div>

      {/* Revealed into space the row keeps for it; never pushes the text. */}
      <span className="ma-slot flex shrink-0 items-center gap-0.5 pt-px" data-pinned={picking ? "" : undefined}>
        {!item.done && (
          <Tooltip label={t("actions.snoozeTomorrow")} keys="H" hint={t("help.snooze")}>
          <button
            type="button"
            data-testid="action-snooze"
            aria-label={t("actions.snoozeTomorrow")}
            onClick={() => snooze(tomorrow, t("actions.dueTomorrow"))}
            className="grid size-5.5 place-items-center rounded-xs text-tertiary hover:bg-a-200 hover:text-primary"
          >
            <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.5]">
              <path d="M13.5 8a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0ZM8 5v3.2l2 1.2" />
            </svg>
          </button>
          </Tooltip>
        )}
        <Menu label={t("meeting.more")} items={items} testid="action-menu" small />
      </span>

      <span className="flex shrink-0 items-center gap-2 pt-px">
        {!item.done ? (
          <Tooltip label={item.due ?? t("actions.addDate")} hint={t("help.due")}>
          <button
            ref={dueRef}
            type="button"
            data-testid={item.due_at ? "action-due" : "action-add-date"}
            data-late={late ? "true" : undefined}
            onClick={(event) => setPicking(picking ? null : event.currentTarget)}
            className={
              item.due_at
                ? `inline-flex h-5 items-center rounded-full px-2 text-2xs tabular-nums ${
                    late
                      ? "bg-danger-quiet text-danger"
                      : "bg-surface-3 text-secondary hover:bg-a-300"
                  }`
                : "inline-flex h-5 items-center rounded-full border border-dashed border-line-strong px-2 text-2xs text-tertiary opacity-0 group-hover:opacity-100 hover:text-primary focus-visible:opacity-100"
            }
          >
            {item.due_at ? dueLabel(item.due_at, locale, words) : `+ ${t("actions.addDate")}`}
          </button>
          </Tooltip>
        ) : null}
        {/*
         * The owner. On the meeting page with a name beside the initials, since the
         * page is about who took what on; in the inbox the initials alone, since the
         * tab already says whose list this is.
         */}
        <span data-testid="action-owner" className="flex items-center gap-1.5 text-xs text-secondary" title={owner}>
          <span
            aria-hidden="true"
            className="grid size-5 place-items-center rounded-full text-[9px] font-semibold text-on-speaker"
            style={{ background: personColour(item.who, item.mine) }}
          >
            {initials(owner)}
          </span>
          {!showMeeting && <bdi>{item.mine ? owner : item.who.split(" ")[0]}</bdi>}
        </span>
      </span>

      {picking && (
        <DatePicker
          value={item.due_at}
          anchor={picking}
          onClose={() => setPicking(null)}
          onPick={(value) => {
            setPicking(null);
            patch.mutate({ item, patch: { due_at: value } });
          }}
        />
      )}
      {context.menu}
    </li>
  );
}
