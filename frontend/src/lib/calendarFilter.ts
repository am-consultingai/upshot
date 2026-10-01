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
  const show = useMutation({
    mutationFn: ({ id, visible }: { id: string; visible: boolean }) =>
      api.calendarSetVisible(id, visible),
    onSuccess: (next) => {
      queryClient.setQueryData(["calendar"], next);
      void queryClient.invalidateQueries();
    },
  });
  return {
    accounts,
    /** Something is hidden: the trigger says so. */
    active: accounts.some((account) => !account.visible),
    checked: (id: string) => accounts.find((account) => account.id === id)?.visible ?? true,
    toggle: (id: string, visible: boolean) => show.mutate({ id, visible }),
  };
}

export type CalendarFilter = ReturnType<typeof useCalendarFilter>;
