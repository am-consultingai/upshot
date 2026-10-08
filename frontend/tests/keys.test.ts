import { describe, expect, it } from "vitest";
import { shortcutKey } from "../src/lib/keys";

/**
 * A letter shortcut has to survive the keyboard being set to Hebrew, where the J key
 * types "ח" and `event.key` never says "j".
 */
describe("shortcutKey", () => {
  it("takes a Latin key as typed", () => {
    expect(shortcutKey({ key: "j", code: "KeyJ" })).toBe("j");
    expect(shortcutKey({ key: "J", code: "KeyJ" })).toBe("j");
    expect(shortcutKey({ key: "/", code: "Slash" })).toBe("/");
  });

  it("falls back to the physical key on a Hebrew layout", () => {
    expect(shortcutKey({ key: "ח", code: "KeyJ" })).toBe("j");
    expect(shortcutKey({ key: "ל", code: "KeyK" })).toBe("k");
    expect(shortcutKey({ key: "ע", code: "KeyG" })).toBe("g");
    expect(shortcutKey({ key: "ת", code: "Comma" })).toBe(",");
  });

  it("keeps a layout's own Latin letters", () => {
    // AZERTY: the key in QWERTY's A place types "q", and q is what was meant.
    expect(shortcutKey({ key: "q", code: "KeyA" })).toBe("q");
  });

  it("leaves named keys and Space alone", () => {
    expect(shortcutKey({ key: "ArrowDown", code: "ArrowDown" })).toBe("ArrowDown");
    expect(shortcutKey({ key: "Enter", code: "Enter" })).toBe("Enter");
    expect(shortcutKey({ key: " ", code: "Space" })).toBe(" ");
  });
});
