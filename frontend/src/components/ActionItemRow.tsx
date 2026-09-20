import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ActionItem } from "../api";
import { useI18n } from "../i18n";

/**
 * One commitment, ticked in place.
 *
 * The tick is the user's and the text is the model's, which is why the two are
 * stored apart: re-summarizing rewrites every row and carries the done state across
 * by the text it matched. A checkbox that silently reopened itself because the
 * prompt was edited would be worse than no checkbox at all.
 *
 * Optimistic, because a checkbox that waits for a round trip before it moves does
 * not read as a checkbox. The invalidation that follows corrects it if the write
 * lost.
 */
export default function ActionItemRow({
  item,
  showMeeting = true,
}: {
  item: ActionItem;
  /** Off on the meeting page, where the meeting is the page you are already on. */
  showMeeting?: boolean;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();

  const toggle = useMutation({
    mutationFn: (done: boolean) => api.setActionDone(item.id, done),
    onMutate: async (done: boolean) => {
      await queryClient.cancelQueries({ queryKey: ["action-items"] });
      const previous = queryClient.getQueriesData({ queryKey: ["action-items"] });
      queryClient.setQueriesData(
        { queryKey: ["action-items"] },
        (old: { items: ActionItem[]; count: number; open: number } | undefined) =>
          old && {
            ...old,
            items: old.items.map((row) => (row.id === item.id ? { ...row, done } : row)),
            open: old.open + (done ? -1 : 1),
          },
      );
      return { previous };
    },
    onError: (_error, _done, context) => {
      for (const [key, value] of context?.previous ?? []) {
        queryClient.setQueryData(key, value);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["action-items"] });
      void queryClient.invalidateQueries({ queryKey: ["meeting", item.meeting_id] });
    },
  });

  const when = item.meeting_started_at
    ? new Date(item.meeting_started_at).toLocaleDateString(locale, {
        day: "numeric",
        month: "short",
      })
    : null;

  return (
    <li
      data-testid="action-item"
      data-action-id={item.id}
      data-done={item.done ? "true" : "false"}
      data-mine={item.mine ? "true" : "false"}
      className="group flex items-start gap-2.5 rounded-md px-2 py-1.5 hover:bg-surface-2"
    >
      <input
        type="checkbox"
        data-testid="action-toggle"
        checked={item.done}
        aria-label={item.done ? t("actions.markOpen") : t("actions.markDone")}
        title={item.done ? t("actions.markOpen") : t("actions.markDone")}
        onChange={(event) => toggle.mutate(event.target.checked)}
        className="mt-0.5 size-3.5 shrink-0 accent-[var(--color-accent)]"
      />
      <div className="min-w-0 flex-1">
        <span
          data-testid="action-what"
          className={`block text-sm ${item.done ? "text-tertiary line-through" : "text-primary"}`}
        >
          {item.what}
        </span>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-2xs text-tertiary">
          {item.due && (
            <span data-testid="action-due" className="text-secondary">
              {t("actions.due")} {item.due}
            </span>
          )}
          {item.due && showMeeting && <span className="opacity-50">·</span>}
          {showMeeting && (
            <Link
              data-testid="action-meeting"
              to={`/m/${item.meeting_id}`}
              className="truncate hover:text-primary hover:underline"
            >
              {item.meeting_title ?? item.meeting_id}
            </Link>
          )}
          {showMeeting && when && (
            <>
              <span className="opacity-50">·</span>
              <span className="tabular-nums">{when}</span>
            </>
          )}
        </div>
      </div>
    </li>
  );
}
