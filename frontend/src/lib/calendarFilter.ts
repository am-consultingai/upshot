import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useCalendarAccounts } from "../components/AccountDots";

/**
 * "Filter by calendar" (D82): the one place an account is shown or hidden.
 *
 * Unticking an account hides it everywhere in Upshot — the list, the calendar view,
 * search, the assistant, reminders and matching — and deletes nothing; ticking it again
 * brings it all back. The server owns the rule, so every pane follows it on its own.
 */
export function useCalendarFilter() {
  const queryClient = useQueryClient();
  const accounts = useCalendarAccounts();
  // What was just clicked, until the server answers. The box follows the click at once,
  // and a refetch of the old status that lands in between cannot tick it back; a refusal
  // drops the entry, so the box returns to what the server says.
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const show = useMutation({
    mutationFn: ({ id, visible }: { id: string; visible: boolean }) =>
      api.calendarSetVisible(id, visible),
    onSuccess: (next) => {
      queryClient.setQueryData(["calendar"], next);
    },
    onSettled: (_data, _error, { id, visible }) => {
      // A later click on the same box is still on its way: leave its entry alone.
      setPending(({ [id]: held, ...rest }) => (held === visible ? rest : { ...rest, [id]: held }));
      void queryClient.invalidateQueries();
    },
  });
  const visible = (id: string, server: boolean) => pending[id] ?? server;
  return {
    accounts,
    /** Something is hidden: the trigger says so. */
    active: accounts.some((account) => !visible(account.id, account.visible)),
    checked: (id: string) =>
      visible(id, accounts.find((account) => account.id === id)?.visible ?? true),
    toggle: (id: string, next: boolean) => {
      setPending((held) => ({ ...held, [id]: next }));
      show.mutate({ id, visible: next });
    },
  };
}

export type CalendarFilter = ReturnType<typeof useCalendarFilter>;
