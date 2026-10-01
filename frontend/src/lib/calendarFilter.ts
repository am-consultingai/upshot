import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Settings } from "../api";
import { useCalendarAccounts } from "../components/AccountDots";

/**
 * "Filter by calendar" (D82): which accounts the library list and the calendar view show.
 *
 * A view only. Unlike hiding an account it leaves search, the assistant and everything
 * else alone. One choice for both panes, so the list and the grid beside it never
 * disagree; remembered in `ui.library_accounts` the way the calendar's span is. Every
 * calendar ticked is no filter at all, which is also what an empty saved list means.
 * "none" stands for recordings on no calendar.
 */
export function useCalendarFilter() {
  const queryClient = useQueryClient();
  const accounts = useCalendarAccounts().filter((account) => account.visible);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const saved =
    ((settings.data?.config ?? {}) as { ui?: { library_accounts?: string[] } }).ui
      ?.library_accounts ?? [];
  const every = [...accounts.map((account) => account.id), "none"];
  // Ids of accounts since hidden or removed fall out on their own.
  const kept = saved.filter((id) => every.includes(id));
  const active = kept.length > 0 && kept.length < every.length;
  const remember = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: (next) => queryClient.setQueryData(["settings"], next),
  });
  const set = (next: string[]) => {
    const all = next.length === 0 || every.every((id) => next.includes(id));
    const value = all ? [] : next;
    // Applied to the shared settings at once, so both panes redraw together and a
    // toggle never waits on a round trip.
    queryClient.setQueryData<Settings>(["settings"], (old) =>
      old
        ? {
            ...old,
            config: {
              ...old.config,
              ui: { ...((old.config.ui as Record<string, unknown>) ?? {}), library_accounts: value },
            },
          }
        : old,
    );
    remember.mutate({ "ui.library_accounts": value });
  };
  return {
    accounts,
    active,
    /** What the meetings endpoint is asked for: undefined is everything. */
    query: active ? kept : undefined,
    checked: (id: string) => !active || kept.includes(id),
    toggle: (id: string, on: boolean) => {
      const current = active ? kept : every;
      set(on ? [...current, id] : current.filter((item) => item !== id));
    },
    /** Whether a calendar event is on one of the chosen accounts. */
    showsEvent: (event: { account_id?: string; accounts?: string[] }) =>
      !active || eventOnAccounts(event, kept),
  };
}

/** An event is shown when any account it is on was chosen (a shared meeting counts once). */
export function eventOnAccounts(
  event: { account_id?: string; accounts?: string[] },
  chosen: string[],
): boolean {
  const on = event.accounts?.length ? event.accounts : event.account_id ? [event.account_id] : [];
  return on.some((id) => chosen.includes(id));
}

export type CalendarFilter = ReturnType<typeof useCalendarFilter>;
