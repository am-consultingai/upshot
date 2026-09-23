import { describe, expect, it } from "vitest";
import type { LlmProvider } from "../src/api";
import { summarizerMissing } from "../src/lib/setup";

const provider = (over: Partial<LlmProvider>): LlmProvider => ({
  id: "anthropic",
  label: "Anthropic",
  needs: "key",
  ready: false,
  ...over,
});

describe("summarizerMissing", () => {
  it("flags a chosen key provider with no key", () => {
    expect(summarizerMissing("anthropic", [provider({ ready: false })])).toBe(true);
    expect(summarizerMissing("anthropic", [provider({ ready: true })])).toBe(false);
  });

  it("flags the subscription when installed but signed out, not when the build cannot tell", () => {
    const cli = { id: "claude-subscription", needs: "cli", ready: true };
    expect(summarizerMissing("claude-subscription", [provider({ ...cli, signed_in: false })])).toBe(true);
    expect(summarizerMissing("claude-subscription", [provider({ ...cli, signed_in: true })])).toBe(false);
    expect(summarizerMissing("claude-subscription", [provider({ ...cli, signed_in: null })])).toBe(false);
    expect(
      summarizerMissing("claude-subscription", [provider({ ...cli, ready: false, signed_in: null })]),
    ).toBe(true);
  });

  it("judges the chosen provider, not whether any other one is set up", () => {
    const others = [provider({ id: "gemini", ready: true }), provider({ id: "anthropic", ready: false })];
    expect(summarizerMissing("anthropic", others)).toBe(true);
  });

  it("says nothing about a provider it does not list, such as the development fake", () => {
    expect(summarizerMissing("fake", [provider({})])).toBe(false);
    expect(summarizerMissing(undefined, [])).toBe(false);
  });
});
