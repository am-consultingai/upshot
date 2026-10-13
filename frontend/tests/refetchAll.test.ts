import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { refetchAll, refetchAllSoon } from "../src/lib/refetchAll";

function seeded(): QueryClient {
  const client = new QueryClient();
  for (const key of [["llm-status"], ["meetings"], ["status"], ["calendar"]]) {
    client.setQueryData(key, { seeded: true });
  }
  return client;
}

function invalidated(client: QueryClient): string[] {
  return client
    .getQueryCache()
    .getAll()
    .filter((query) => query.state.isInvalidated)
    .map((query) => String(query.queryKey[0]))
    .sort();
}

describe("refetchAll", () => {
  // The calendar's sync sends an event every minute; refetching the provider status
  // with it ran the three CLIs all day and flashed a console window (machine A).
  it("refetches everything but the provider status", () => {
    const client = seeded();
    refetchAll(client);
    expect(invalidated(client)).toEqual(["calendar", "meetings", "status"]);
  });

  it("leaves the provider status alone when it waits for a burst too", () => {
    vi.useFakeTimers();
    try {
      const client = seeded();
      refetchAllSoon(client);
      refetchAllSoon(client);
      expect(invalidated(client)).toEqual([]);
      vi.advanceTimersByTime(200);
      expect(invalidated(client)).toEqual(["calendar", "meetings", "status"]);
    } finally {
      vi.useRealTimers();
    }
  });
});
