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

describe("a piece of text runs the way its words do", () => {
  it("Hebrew with English terms in it is a Hebrew sentence", () => {
    expect(directionOf("לשלוח את הסיכום ל-Slack")).toBe("rtl");
    expect(directionOf("PR חדש לתיקון הבאג")).toBe("rtl");
    expect(directionOf("Slack: לשלוח את הסיכום לצוות")).toBe("rtl");
    expect(directionOf("לעדכן את ה-README ב-GitHub")).toBe("rtl");
    expect(directionOf("לבדוק את ה-Dashboard ב-Grafana")).toBe("rtl");
    expect(directionOf("לשלוח ל-Microsoft את ה-NDA")).toBe("rtl");
    // Opening with a Hebrew word, however many English ones follow.
    expect(directionOf("להגדיר Google Analytics")).toBe("rtl");
    expect(directionOf("לבדוק את Google Cloud Run Jobs API")).toBe("rtl");
    expect(directionOf("לחדש מנוי Adobe Creative Cloud")).toBe("rtl");
    expect(directionOf("إرسال الملخص")).toBe("rtl");
  });

  it("English and other left-to-right scripts run left to right, also around a Hebrew name", () => {
    expect(directionOf("Call Dana.")).toBe("ltr");
    expect(directionOf("Send דנה the notes")).toBe("ltr");
    expect(directionOf("会议纪要 notes עם")).toBe("ltr");
    expect(directionOf("बैठक के नोट्स")).toBe("ltr");
  });

  it("inside a right-to-left block, a Hebrew prefix or a tie leans right to left; an English sentence stays English", () => {
    expect(directionOf("PR ל-GitHub Actions", "rtl")).toBe("rtl");
    expect(directionOf("PR ל-GitHub Actions")).toBe("ltr");
    expect(directionOf("API ב-Grafana Cloud Dashboards", "rtl")).toBe("rtl");
    expect(directionOf("ab אב", "rtl")).toBe("rtl");
    expect(directionOf("Call Dana.", "rtl")).toBe("ltr");
    expect(directionOf("Send דנה the notes", "rtl")).toBe("ltr");
    expect(directionOf("Follow up with משה on the budget", "rtl")).toBe("ltr");
    expect(directionOf("Book a re-run of the build", "rtl")).toBe("ltr");
    // Hebrew function words and the maqaf mark a Hebrew sentence.
    expect(directionOf("Pull request עם fix לבאג", "rtl")).toBe("rtl");
    expect(directionOf("Code review לפני merge", "rtl")).toBe("rtl");
    expect(directionOf("Sync עם Dana על Q3 roadmap", "rtl")).toBe("rtl");
    expect(directionOf("Update the Jira ticket של הבאג", "rtl")).toBe("rtl");
    expect(directionOf("PR review עם Dana", "rtl")).toBe("rtl");
    expect(directionOf("Deploy ל\u05BEstaging", "rtl")).toBe("rtl");
  });

  it("digits, punctuation and symbols do not count; nothing at all is left to right", () => {
    expect(directionOf("3. לשלוח")).toBe("rtl");
    expect(directionOf("٣ items")).toBe("ltr");
    expect(directionOf("× ÷ — 42")).toBe("ltr");
    expect(directionOf("")).toBe("ltr");
  });

  it("a tie goes to the first word", () => {
    expect(directionOf("ab אב")).toBe("ltr");
    expect(directionOf("אב ab")).toBe("rtl");
  });
});
