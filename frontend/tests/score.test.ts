import { describe, expect, it } from "vitest";
import { boost, score } from "../src/lib/score";

describe("score", () => {
  it("prefers a word boundary to a scavenged match", () => {
    // The failure this weighting exists to prevent: "sum" finding its letters
    // scattered through an unrelated string and outranking the obvious answer.
    expect(score("Summarize again", "sum")).toBeGreaterThan(score("Set up my usual mic", "sum"));
  });

  it("prefers a contiguous run to a jump", () => {
    expect(score("record", "rec")).toBeGreaterThan(score("re-open a call", "rec"));
  });

  it("matches across word boundaries, which is how initials work", () => {
    expect(score("Start recording", "sr")).toBeGreaterThan(0);
  });

  it("returns zero when a character is missing", () => {
    expect(score("Settings", "zq")).toBe(0);
  });

  it("returns zero when the query is longer than the candidate", () => {
    expect(score("mic", "microphone")).toBe(0);
  });

  it("treats an empty query as a match, so the resting list is everything", () => {
    expect(score("anything", "  ")).toBe(1);
  });

  it("is case-insensitive but mildly prefers the exact case", () => {
    expect(score("Summary", "Sum")).toBeGreaterThan(score("Summary", "sum"));
  });
});

describe("boost", () => {
  const now = Date.UTC(2026, 8, 19);

  it("leaves something never chosen exactly as it scored", () => {
    expect(boost(0, 0, now)).toBe(1);
  });

  it("lifts something chosen often and recently", () => {
    expect(boost(20, now, now)).toBeGreaterThan(boost(1, now, now));
  });

  it("decays, so last month's habit stops deciding today's order", () => {
    const monthAgo = now - 30 * 86_400_000;
    expect(boost(20, monthAgo, now)).toBeLessThan(boost(20, now, now));
  });

  it("never reaches zero, because a boost must reorder and never remove", () => {
    // An item that matched and then gets multiplied to nothing disappears, and
    // whoever typed its exact name is left looking at an empty list.
    expect(boost(1000, 0, now)).toBeGreaterThanOrEqual(1);
    expect(boost(1000, now, now)).toBeLessThanOrEqual(1.6);
  });
});
