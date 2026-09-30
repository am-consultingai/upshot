import { describe, expect, it } from "vitest";
import { greeting, greetingKey } from "../src/lib/greeting";
import { en } from "../src/locales/en";
import { he } from "../src/locales/he";

const inEnglish = (key: keyof typeof en) => en[key];
const inHebrew = (key: keyof typeof en) => he[key];

describe("the greeting", () => {
  it("follows the time of day", () => {
    expect(greetingKey(8)).toBe("greeting.morning");
    expect(greetingKey(14)).toBe("greeting.afternoon");
    expect(greetingKey(20)).toBe("greeting.evening");
    expect(greetingKey(2)).toBe("greeting.evening");
  });

  it("uses the name when Google gave one", () => {
    expect(greeting(inEnglish, 9, "Dana")).toBe("Good morning, Dana");
    expect(greeting(inHebrew, 9, "Dana")).toBe("בוקר טוב, Dana");
  });

  it("greets without a name when there is none", () => {
    expect(greeting(inEnglish, 15, null)).toBe("Good afternoon");
    expect(greeting(inEnglish, 21, "  ")).toBe("Good evening");
  });
});
