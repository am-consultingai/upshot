import { describe, expect, it } from "vitest";
import { meetingDirections, textDirection } from "../src/lib/direction";

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
