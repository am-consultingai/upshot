import { describe, expect, it } from "vitest";
import { directionOf, meetingDirections, textDirection } from "../src/lib/direction";

describe("text direction comes from the API", () => {
  it("is rtl only when the API says so", () => {
    expect(textDirection("rtl")).toBe("rtl");
    expect(textDirection("ltr")).toBe("ltr");
    expect(textDirection(undefined)).toBe("ltr");
    expect(textDirection(null)).toBe("ltr");
  });

  it("an Arabic meeting runs right to left, a Spanish one left to right", () => {
    expect(meetingDirections({ direction: "rtl", summary_direction: "rtl" })).toEqual({ summary: "rtl", transcript: "rtl" });
    expect(meetingDirections({ direction: "ltr", summary_direction: "ltr" })).toEqual({ summary: "ltr", transcript: "ltr" });
  });

  it("the notes follow the summary's language, the transcript the meeting's", () => {
    // A Hebrew meeting summarized in English.
    expect(meetingDirections({ direction: "rtl", summary_direction: "ltr" })).toEqual({ summary: "ltr", transcript: "rtl" });
    expect(meetingDirections({ direction: "rtl" })).toEqual({ summary: "rtl", transcript: "rtl" });
    expect(meetingDirections(undefined)).toEqual({ summary: "ltr", transcript: "ltr" });
  });
});

describe("a piece of text runs the way its first letter does", () => {
  it("Hebrew and Arabic run right to left, even when English comes later", () => {
    expect(directionOf("לשלוח את הסיכום ל-Slack")).toBe("rtl");
    expect(directionOf("إرسال الملخص")).toBe("rtl");
  });

  it("English runs left to right, even when Hebrew comes later", () => {
    expect(directionOf("Call Dana.")).toBe("ltr");
    expect(directionOf("Send דנה the notes")).toBe("ltr");
  });

  it("numbers and punctuation do not decide; nothing at all is left to right", () => {
    expect(directionOf("3. לשלוח")).toBe("rtl");
    expect(directionOf("— 42")).toBe("ltr");
    expect(directionOf("")).toBe("ltr");
  });
});

