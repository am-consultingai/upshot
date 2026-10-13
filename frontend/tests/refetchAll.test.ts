import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import { refetchAfterProviderChange, refetchAll, refetchAllSoon } from "../src/lib/refetchAll";

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

describe("refetchAfterProviderChange", () => {
  // A status fetch begun before a sign-out or a cancel was joined, and showed the state
  // from before it (second review of #26).
  it("does not join a status fetch begun before the change", async () => {
    const client = seeded();
    const fresh = vi.spyOn(api, "llmStatus").mockResolvedValue({ active: "x", providers: [] });
    let finishOld: (value: unknown) => void = () => undefined;
    const old = client.fetchQuery({
      queryKey: ["llm-status"],
      queryFn: () => new Promise((resolve) => (finishOld = resolve)),
    });
    await refetchAfterProviderChange(client);
    expect(fresh).toHaveBeenCalledWith(true);
    expect(client.getQueryData(["llm-status"])).toEqual({ active: "x", providers: [] });
    finishOld({ stale: true });
    await old.catch(() => undefined);
    expect(client.getQueryData(["llm-status"])).toEqual({ active: "x", providers: [] });
    fresh.mockRestore();
  });
});
