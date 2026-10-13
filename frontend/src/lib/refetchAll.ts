import type { Query, QueryClient } from "@tanstack/react-query";

/**
 * Queries a server event does not refetch. The provider status runs Claude's, Codex's
 * and Antigravity's CLIs to answer, and the calendar's sync sends an event every minute:
 * refetching it with everything else ran the three CLIs all day, and one of them
 * (Antigravity's) now and then opens a console window that flashes on screen (machine A,
 * 2026-10-13). It is refetched where it can change: Settings, setup, a sign-in.
 */
const NOT_ON_EVENTS = new Set(["llm-status"]);

function onEvents(query: Query): boolean {
  return !NOT_ON_EVENTS.has(String(query.queryKey[0]));
}

/** Refetch everything a server event can have changed. */
export function refetchAll(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ predicate: onEvents });
}

const waiting = new WeakMap<QueryClient, ReturnType<typeof setTimeout>>();

/**
 * Refetch everything once the calls for it stop coming. A calendar shown or hidden asks
 * twice, once when its request returns and once when the server's event arrives, so ten
 * quick clicks in "Filter by calendar" refetched every query on the page twenty times
 * (z8tj1hck4w). Each call restarts the wait; the last one refetches.
 */
export function refetchAllSoon(queryClient: QueryClient, wait = 200): void {
  clearTimeout(waiting.get(queryClient));
  waiting.set(
    queryClient,
    setTimeout(() => {
      waiting.delete(queryClient);
      refetchAll(queryClient);
    }, wait),
  );
}
