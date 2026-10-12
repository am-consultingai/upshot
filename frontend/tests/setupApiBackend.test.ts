import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  settings: vi.fn(),
  calendarStatus: vi.fn(),
  diagnostics: vi.fn(),
  llmStatus: vi.fn(),
  llmSignin: vi.fn(),
  llmSigninCode: vi.fn(),
  reportSetupFailure: vi.fn(),
}));
vi.mock("../src/api", () => ({ api }));

import { ApiSetupBackend } from "../src/setup/apiBackend";

const SIGNED_OUT = {
  providers: [
    { id: "claude-subscription", ready: true, signed_in: false, console_open: true },
    { id: "antigravity-subscription", ready: true, signed_in: false, console_open: true },
  ],
};

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.settings.mockResolvedValue({ config: {} });
  api.calendarStatus.mockResolvedValue(null);
  api.diagnostics.mockResolvedValue(null);
  api.llmStatus.mockResolvedValue(SIGNED_OUT);
  api.reportSetupFailure.mockResolvedValue({ reported: true });
});

afterEach(() => vi.useRealTimers());

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("ApiSetupBackend: a sign-in that does not finish is reported (machine A, 2026-10-12)", () => {
  it("a refused sign-in fails the card and goes to the crash reports with its stage and reason", async () => {
    api.llmSignin.mockRejectedValue(new Error("500"));
    const backend = await ApiSetupBackend.create();
    backend.signIn("antigravity");
    await flush();
    expect(backend.snapshot().cli.antigravity.phase).toBe("signin-failed");
    expect(api.reportSetupFailure).toHaveBeenCalledWith({
      provider: "antigravity-subscription",
      stage: "signin",
      reason: "refused",
    });
    backend.dispose();
  });

  it("a sign-in that ends signed out says so too", async () => {
    api.llmSignin.mockResolvedValue({ launched: true });
    const backend = await ApiSetupBackend.create();
    backend.signIn("antigravity");
    await flush();
    api.llmStatus.mockResolvedValue({
      providers: [{ id: "antigravity-subscription", ready: true, signed_in: false, console_open: false }],
    });
    await vi.waitFor(() => expect(backend.snapshot().cli.antigravity.phase).toBe("signin-failed"), { timeout: 5000 });
    expect(api.reportSetupFailure).toHaveBeenCalledWith({
      provider: "antigravity-subscription",
      stage: "signin",
      reason: "ended-signed-out",
    });
    backend.dispose();
  });

  it("Antigravity's code goes into its own window; Claude's into the page", async () => {
    const backend = await ApiSetupBackend.create();
    await flush();
    expect(backend.snapshot().cli.antigravity.codeInWindow).toBe(true);
    expect(backend.snapshot().cli.claude.codeInWindow).toBe(false);
    backend.dispose();
  });
});
