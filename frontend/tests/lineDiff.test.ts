import { describe, expect, it } from "vitest";
import { hunks, lineDiff } from "../src/lib/lineDiff";

describe("lineDiff", () => {
  it("marks nothing when nothing changed", () => {
    expect(lineDiff("a\nb", "a\nb").every((line) => line.kind === "same")).toBe(true);
  });

  it("shows a replaced line as removed then added", () => {
    expect(lineDiff("one\ntwo\nthree", "one\n2\nthree")).toEqual([
      { kind: "same", text: "one" },
      { kind: "remove", text: "two" },
      { kind: "add", text: "2" },
      { kind: "same", text: "three" },
    ]);
  });

  it("finds lines added at the end and removed from the start", () => {
    const diff = lineDiff("intro\nbody", "body\nmore");
    expect(diff.filter((line) => line.kind === "remove").map((line) => line.text)).toEqual(["intro"]);
    expect(diff.filter((line) => line.kind === "add").map((line) => line.text)).toEqual(["more"]);
  });
});

describe("hunks", () => {
  it("folds unchanged runs away, keeping context around a change", () => {
    const before = ["1", "2", "3", "4", "5", "6", "7", "8"].join("\n");
    const after = ["1", "2", "3", "4", "5", "6", "7", "eight"].join("\n");
    const shown = hunks(lineDiff(before, after), 1);
    expect(shown[0]).toBeNull();
    expect(shown.slice(1).map((line) => line && `${line.kind}:${line.text}`)).toEqual([
      "same:7",
      "remove:8",
      "add:eight",
    ]);
  });
});
