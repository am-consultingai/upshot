import type { QueryClient } from "@tanstack/react-query";

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
      void queryClient.invalidateQueries();
    }, wait),
  );
}
