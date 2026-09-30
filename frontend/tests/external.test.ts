import { describe, expect, it } from "vitest";
import { isExternal } from "../src/lib/external";

describe("which links leave the app", () => {
  const origin = "http://127.0.0.1:8078";
  it("another site does", () => {
    expect(isExternal("https://myaccount.google.com/connections", origin)).toBe(true);
    expect(isExternal("http://example.com/x", origin)).toBe(true);
  });
  it("the app's own pages do not", () => {
    expect(isExternal("/settings#calendar", origin)).toBe(false);
    expect(isExternal("http://127.0.0.1:8078/m/1", origin)).toBe(false);
  });
  it("nothing but http(s) is opened", () => {
    expect(isExternal("mailto:someone@example.com", origin)).toBe(false);
    expect(isExternal("file:///C:/Windows/notepad.exe", origin)).toBe(false);
  });
});
