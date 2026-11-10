import { describe, expect, it } from "vitest";
import { watchFinished } from "../src/components/ProviderSettings";
import { alternatives, canRunNow } from "../src/components/SignedOutCard";
import type { LlmProvider } from "../src/api";

const launchedAt = 1_000;

describe("watchFinished", () => {
  it("ends the wait once the CLI is signed in", () => {
    expect(watchFinished({ signedIn: true, consoleOpen: true, checkedAt: 2_000, launchedAt })).toBe(true);
  });

  it("ends the wait when the window the login opened was closed", () => {
    expect(watchFinished({ signedIn: false, consoleOpen: false, checkedAt: 2_000, launchedAt })).toBe(true);
  });

  it("keeps waiting while the window is open", () => {
    expect(watchFinished({ signedIn: false, consoleOpen: true, checkedAt: 2_000, launchedAt })).toBe(false);
  });

  it("ignores a status fetched before the launch, which knew of no window yet", () => {
    expect(watchFinished({ signedIn: false, consoleOpen: false, checkedAt: 500, launchedAt })).toBe(false);
  });

  it("leaves an older server that does not report the window to the timeout", () => {
    expect(watchFinished({ signedIn: false, consoleOpen: undefined, checkedAt: 2_000, launchedAt })).toBe(false);
  });

  it("keeps watching a CLI that reads signed in while a summary still waits for it", () => {
    expect(
      watchFinished({ signedIn: true, waiting: true, consoleOpen: true, checkedAt: 2_000, launchedAt }),
    ).toBe(false);
  });

  it("ends it once the waiting summary has gone through", () => {
    expect(
      watchFinished({ signedIn: true, waiting: false, consoleOpen: true, checkedAt: 2_000, launchedAt }),
    ).toBe(true);
  });

  it("still ends it when the sign-in window is closed", () => {
    expect(
      watchFinished({ signedIn: true, waiting: true, consoleOpen: false, checkedAt: 2_000, launchedAt }),
    ).toBe(true);
  });
});

describe("canRunNow (the signed-out card's alternatives)", () => {
  const row = (fields: Partial<LlmProvider>): LlmProvider => ({ id: "x", label: "X", needs: "cli", ready: true, ...fields });

  it("offers a signed-in CLI", () => {
    expect(canRunNow(row({ signed_in: true }))).toBe(true);
  });

  it("not a CLI another summary already waits on, whatever its status reads", () => {
    expect(canRunNow(row({ signed_in: true, waiting: true }))).toBe(false);
  });

  it("not a signed-out or uninstalled CLI", () => {
    expect(canRunNow(row({ signed_in: false }))).toBe(false);
    expect(canRunNow(row({ signed_in: true, ready: false }))).toBe(false);
  });

  it("an API provider only with its key", () => {
    expect(canRunNow(row({ id: "gemini", needs: "key", ready: true }))).toBe(true);
    expect(canRunNow(row({ id: "gemini", needs: "key", ready: false }))).toBe(false);
  });

  it("never the local model or transcripts only", () => {
    expect(canRunNow(row({ id: "ollama", needs: "ollama" }))).toBe(false);
    expect(canRunNow(row({ id: "none", needs: "none" }))).toBe(false);
  });
});

describe("alternatives (what the signed-out card offers)", () => {
  const cli = (id: string, fields: Partial<LlmProvider> = {}): LlmProvider => ({
    id, label: id, needs: "cli", ready: true, signed_in: true, ...fields,
  });
  const rows = [cli("claude-subscription", { signed_in: false }), cli("codex-subscription"), cli("antigravity-subscription")];
  const ids = (list: LlmProvider[]) => list.map((row) => row.id);

  it("offers the ready others when the usual provider is signed out", () => {
    const at = { waitingFor: "claude-subscription", active: "claude-subscription", fallback: "", sensitive: false };
    expect(ids(alternatives(rows, at))).toEqual(["codex-subscription", "antigravity-subscription"]);
  });

  it("not the usual provider when the job waits on the fallback (its allowance is spent)", () => {
    const at = { waitingFor: "antigravity-subscription", active: "codex-subscription", fallback: "antigravity-subscription", sensitive: false };
    expect(ids(alternatives(rows, at))).toEqual([]);
  });

  it("nothing for a sensitive meeting", () => {
    const at = { waitingFor: "claude-subscription", active: "claude-subscription", fallback: "", sensitive: true };
    expect(alternatives(rows, at)).toEqual([]);
  });
});
