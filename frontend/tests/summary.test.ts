import { describe, expect, it } from "vitest";
import { leadFirst } from "../src/lib/summary";

describe("leadFirst", () => {
  it("drops a generic first heading so the paragraph under it leads", () => {
    expect(leadFirst("<h2>Overview</h2><p>Activation is down.</p><h2>Decisions</h2>")).toBe(
      "<p>Activation is down.</p><h2>Decisions</h2>",
    );
  });

  it("does it in Hebrew too", () => {
    expect(leadFirst("<h2>סקירה כללית</h2><p>ההפעלה ירדה.</p>")).toBe("<p>ההפעלה ירדה.</p>");
  });

  it("drops a first heading that only repeats the meeting's title", () => {
    expect(leadFirst("<h1>Weekly sync</h1><p>Nothing blocking.</p>", "Weekly sync")).toBe(
      "<p>Nothing blocking.</p>",
    );
  });

  it("keeps a heading that says something", () => {
    const html = "<h2>Decisions</h2><p>Roll it back.</p>";
    expect(leadFirst(html)).toBe(html);
  });

  it("keeps a generic heading with no paragraph to lead after it", () => {
    const html = "<h2>Overview</h2><ul><li>One</li></ul>";
    expect(leadFirst(html)).toBe(html);
  });

  it("leaves a document that already opens with its lead alone", () => {
    const html = "<p>Activation is down.</p><h2>Decisions</h2>";
    expect(leadFirst(html)).toBe(html);
  });

  it("looks inside the renderer's wrapper", () => {
    expect(leadFirst('<div class="ma-free"><h2>Summary</h2><p>Lead.</p></div>')).toBe("<p>Lead.</p>");
  });
});
