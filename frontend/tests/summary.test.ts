import { describe, expect, it } from "vitest";
import { leadFirst, withCitations } from "../src/lib/summary";

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

describe("withCitations", () => {
  const label = (time: string) => `Play from ${time}`;
  const cites = (html: string) =>
    [...new DOMParser().parseFromString(withCitations(html, label), "text/html").querySelectorAll("button")];

  it("leaves a summary without citations exactly as it was", () => {
    const html = '<div class="ma-free"><p>Lead.</p><ul><li>One</li></ul></div>';
    expect(withCitations(html, label)).toBe(html);
  });

  it("ends a cited point with a labelled control at its moment", () => {
    const [button] = cites('<ul><li data-at-ms="754000">Pricing moves to Q3</li></ul>');
    expect(button.textContent).toBe("(00:12:34)");
    expect(button.getAttribute("aria-label")).toBe("Play from 00:12:34");
    expect(button.dataset.cite).toBe("754000");
    expect(button.type).toBe("button");
    expect(button.previousSibling?.textContent).toBe("Pricing moves to Q3");
  });

  it("cites a single-point paragraph, and nothing without the attribute", () => {
    expect(cites('<p data-at-ms="1000">A</p><p>B</p><li data-at-ms="x">C</li>')).toHaveLength(1);
  });

  it("puts the moment before a nested list, with the point's own words", () => {
    const [button] = cites('<ul><li data-at-ms="5000">Parent<ul><li>child</li></ul></li></ul>');
    expect(button.nextElementSibling?.tagName).toBe("UL");
  });
});
