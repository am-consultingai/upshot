import type { Query, QueryClient } from "@tanstack/react-query";
import { api } from "../api";

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

/**
 * After a provider was signed in or out, installed, chosen or given a key: the rest
 * refetched, and the provider status asked fresh by name. Not through the key's own
 * queryFn, which the sidebar shares with a held-answer one (review of #26); and a status
 * already being fetched began before the change, so it is cancelled rather than joined
 * (second review of #26).
 */
export async function refetchAfterProviderChange(queryClient: QueryClient): Promise<unknown> {
  const status = async () => {
    await queryClient.cancelQueries({ queryKey: ["llm-status"] });
    return queryClient.fetchQuery({ queryKey: ["llm-status"], queryFn: () => api.llmStatus(true) });
  };
  return Promise.all([queryClient.invalidateQueries({ predicate: onEvents }), status()]);
}
