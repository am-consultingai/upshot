import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockSetupBackend } from "../src/setup/mockBackend";

/** Setup's calendar step, as the mock backend plays it: accounts are added (D82). */
describe("setup: add another calendar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("a second sign-in adds a second account beside the first", () => {
    const backend = new MockSetupBackend("fresh");
    backend.connectCalendar();
    vi.runAllTimers();
    backend.connectCalendar();
    expect(backend.snapshot().calendar.phase).toBe("waiting");
    expect(backend.snapshot().calendar.accounts).toHaveLength(1);
    vi.runAllTimers();
    const { phase, accounts } = backend.snapshot().calendar;
    expect(phase).toBe("connected");
    expect(accounts.map((account) => account.address)).toEqual([
      "dana.levi@example.com",
      "account2@example.com",
    ]);
  });

  it("cancelling another sign-in keeps the accounts already connected", () => {
    const backend = new MockSetupBackend("fresh");
    backend.connectCalendar();
    vi.runAllTimers();
    backend.connectCalendar();
    backend.cancelCalendar();
    const { phase, accounts } = backend.snapshot().calendar;
    expect(phase).toBe("connected");
    expect(accounts).toHaveLength(1);
  });

  it("a failed second sign-in says so and keeps the first account", () => {
    const backend = new MockSetupBackend("fresh");
    backend.connectCalendar();
    vi.runAllTimers();
    backend.outcomes.calendar = "partial";
    backend.connectCalendar();
    vi.runAllTimers();
    const { phase, accounts } = backend.snapshot().calendar;
    expect(phase).toBe("partial");
    expect(accounts).toHaveLength(1);
  });

  it("with no account yet, a cancel is a cancel", () => {
    const backend = new MockSetupBackend("fresh");
    backend.connectCalendar();
    backend.cancelCalendar();
    expect(backend.snapshot().calendar.phase).toBe("cancelled");
    expect(backend.snapshot().calendar.accounts).toEqual([]);
  });
});
