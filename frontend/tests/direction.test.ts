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

describe("a piece of text runs the way most of its letters do", () => {
  it("Hebrew and Arabic run right to left, also when they open with an English word", () => {
    expect(directionOf("לשלוח את הסיכום ל-Slack")).toBe("rtl");
    expect(directionOf("PR חדש לתיקון הבאג")).toBe("rtl");
    expect(directionOf("Slack: לשלוח את הסיכום לצוות")).toBe("rtl");
    expect(directionOf("إرسال الملخص")).toBe("rtl");
  });

  it("English and other left-to-right scripts run left to right, also around a Hebrew name", () => {
    expect(directionOf("Call Dana.")).toBe("ltr");
    expect(directionOf("Send דנה the notes")).toBe("ltr");
    expect(directionOf("会议纪要 עם")).toBe("ltr");
    expect(directionOf("会议 עם דנה")).toBe("rtl");
    expect(directionOf("बैठक के नोट्स")).toBe("ltr");
  });

  it("digits, punctuation and symbols do not count; nothing at all is left to right", () => {
    expect(directionOf("3. לשלוח")).toBe("rtl");
    expect(directionOf("٣ items")).toBe("ltr");
    expect(directionOf("× ÷ — 42")).toBe("ltr");
    expect(directionOf("")).toBe("ltr");
  });

  it("a tie goes to the first letter", () => {
    expect(directionOf("ab אב")).toBe("ltr");
    expect(directionOf("אב ab")).toBe("rtl");
  });
});
