import { describe, expect, it } from "vitest";
import { markdownFilename, summaryToMarkdown } from "../src/lib/markdown";

describe("summaryToMarkdown", () => {
  it("turns headings, paragraphs and lists into Markdown", () => {
    const html = `
      <h2>Overview</h2>
      <p>Activation is down <strong>6%</strong> since the new flow.</p>
      <h2>Action items</h2>
      <ul><li>Dana: write the spec</li><li>Yoni: pull the numbers</li></ul>
    `;
    expect(summaryToMarkdown(html)).toBe(
      "## Overview\n\n" +
        "Activation is down **6%** since the new flow.\n\n" +
        "## Action items\n\n" +
        "- Dana: write the spec\n- Yoni: pull the numbers\n",
    );
  });

  it("numbers an ordered list and nests a sublist", () => {
    const html = "<ol><li>first<ul><li>under</li></ul></li><li>second</li></ol>";
    expect(summaryToMarkdown(html)).toBe("1. first\n  - under\n2. second\n");
  });

  it("keeps links, code and emphasis", () => {
    const html = '<p>See <a href="https://x.test">the doc</a> for <code>flag</code> and <em>why</em>.</p>';
    expect(summaryToMarkdown(html)).toBe(
      "See [the doc](https://x.test) for `flag` and *why*.\n",
    );
  });

  it("renders a table with a header separator", () => {
    const html = "<table><tr><th>Who</th><th>What</th></tr><tr><td>Dana</td><td>Spec</td></tr></table>";
    expect(summaryToMarkdown(html)).toBe(
      "| Who | What |\n| --- | --- |\n| Dana | Spec |\n",
    );
  });

  it("escapes a pipe inside a cell so it cannot end the column", () => {
    const html = "<table><tr><th>A</th></tr><tr><td>x | y</td></tr></table>";
    expect(summaryToMarkdown(html)).toContain("| x \\| y |");
  });

  it("quotes a blockquote and fences a pre", () => {
    expect(summaryToMarkdown("<blockquote><p>said it</p></blockquote>")).toBe("> said it\n");
    expect(summaryToMarkdown("<pre>a\nb</pre>")).toBe("```\na\nb\n```\n");
  });

  it("collapses the whitespace HTML would have collapsed", () => {
    expect(summaryToMarkdown("<p>one\n   two</p>")).toBe("one two\n");
  });

  it("drops script and style rather than printing them", () => {
    const html = "<p>kept</p><script>alert(1)</script><style>p{color:red}</style>";
    expect(summaryToMarkdown(html)).toBe("kept\n");
  });

  it("falls back to the text of an element it does not know", () => {
    expect(summaryToMarkdown("<figure><figcaption>a caption</figcaption></figure>")).toBe(
      "a caption\n",
    );
  });

  it("adds the title only when the document has no h1 of its own", () => {
    expect(summaryToMarkdown("<p>body</p>", "Roadmap")).toBe("# Roadmap\n\nbody\n");
    expect(summaryToMarkdown("<h1>Its own</h1><p>body</p>", "Roadmap")).toBe(
      "# Its own\n\nbody\n",
    );
  });

  it("survives an empty document", () => {
    expect(summaryToMarkdown("")).toBe("\n");
  });
});

describe("markdownFilename", () => {
  it("uses the title and strips what a filesystem would refuse", () => {
    expect(markdownFilename('Q4: roadmap/plan?', "m-1")).toBe("Q4- roadmap-plan-.md");
  });

  it("falls back to the id when there is no title", () => {
    expect(markdownFilename(null, "m-1")).toBe("m-1.md");
    expect(markdownFilename("   ", "m-1")).toBe("m-1.md");
  });
});
