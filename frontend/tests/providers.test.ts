import { describe, expect, it } from "vitest";
import { watchFinished } from "../src/components/ProviderSettings";

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
});
