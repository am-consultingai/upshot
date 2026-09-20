/**
 * The summary as Markdown.
 *
 * `Copy` puts rich HTML on the clipboard, which is right for mail and chat and
 * useless for a repo, a ticket or Notion. Converting here rather than on the server
 * keeps it honest about what is actually on the screen: the same sanitized HTML the
 * page rendered is the thing that gets converted.
 *
 * Deliberately small. The summary is written by a prompt that is told to use
 * semantic HTML, so this handles headings, lists, tables, emphasis, links and code
 * — and falls back to the element's text for anything it does not know, which is
 * the right failure for a document whose shape nobody controls.
 */

const BLOCK = new Set([
  "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DIV", "DL", "FIGURE", "FOOTER",
  "H1", "H2", "H3", "H4", "H5", "H6", "HEADER", "MAIN", "OL", "P", "PRE",
  "SECTION", "TABLE", "UL",
]);

function inline(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    // Collapse the whitespace HTML would have collapsed anyway; a newline inside a
    // paragraph is authoring, not content, and in Markdown it would break the line.
    return (node.textContent ?? "").replace(/\s+/g, " ");
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node as Element;
  const inner = children(el, inline).join("");
  switch (el.tagName) {
    case "BR":
      return "\n";
    case "STRONG":
    case "B":
      return inner.trim() ? `**${inner.trim()}**` : "";
    case "EM":
    case "I":
      return inner.trim() ? `*${inner.trim()}*` : "";
    case "CODE":
      return inner.trim() ? `\`${inner.trim()}\`` : "";
    case "DEL":
    case "S":
      return inner.trim() ? `~~${inner.trim()}~~` : "";
    case "A": {
      const href = el.getAttribute("href");
      return href && inner.trim() ? `[${inner.trim()}](${href})` : inner;
    }
    default:
      return inner;
  }
}

function children<T>(el: Element, map: (node: Node) => T): T[] {
  return [...el.childNodes].map(map);
}

function cell(el: Element): string {
  // A pipe inside a cell would end the column; escaping is cheaper than a fallback.
  return inline(el).trim().replace(/\|/g, "\\|");
}

function block(node: Node, depth = 0): string {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = (node.textContent ?? "").replace(/\s+/g, " ").trim();
    return text ? `${text}\n\n` : "";
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node as Element;
  const tag = el.tagName;

  if (tag === "SCRIPT" || tag === "STYLE") return "";

  if (/^H[1-6]$/.test(tag)) {
    const text = inline(el).trim();
    return text ? `${"#".repeat(Number(tag[1]))} ${text}\n\n` : "";
  }
  if (tag === "P") {
    const text = inline(el).trim();
    return text ? `${text}\n\n` : "";
  }
  if (tag === "HR") return "---\n\n";
  if (tag === "PRE") {
    return `\`\`\`\n${(el.textContent ?? "").replace(/\n+$/, "")}\n\`\`\`\n\n`;
  }
  if (tag === "BLOCKQUOTE") {
    const inner = blocks(el, depth).trim();
    if (!inner) return "";
    return `${inner.split("\n").map((line) => `> ${line}`.trimEnd()).join("\n")}\n\n`;
  }
  if (tag === "UL" || tag === "OL") {
    const pad = "  ".repeat(depth);
    const lines: string[] = [];
    let index = 1;
    for (const li of [...el.children].filter((child) => child.tagName === "LI")) {
      const marker = tag === "OL" ? `${index++}.` : "-";
      // A nested list is a block child of the <li>; its own text is inline.
      const own = [...li.childNodes]
        .filter((child) => !(child.nodeType === Node.ELEMENT_NODE && isList(child as Element)))
        .map(inline)
        .join("")
        .trim();
      lines.push(`${pad}${marker} ${own}`.trimEnd());
      for (const child of [...li.children].filter(isList)) {
        const nested = block(child, depth + 1).replace(/\n+$/, "");
        if (nested) lines.push(nested);
      }
    }
    return lines.length ? `${lines.join("\n")}\n\n` : "";
  }
  if (tag === "TABLE") {
    const rows = [...el.querySelectorAll("tr")];
    if (rows.length === 0) return "";
    const grid = rows.map((row) => [...row.children].map(cell));
    const width = Math.max(...grid.map((row) => row.length));
    const pad = (row: string[]) => [...row, ...Array(width - row.length).fill("")];
    const [head, ...body] = grid;
    const lines = [
      `| ${pad(head).join(" | ")} |`,
      `| ${Array(width).fill("---").join(" | ")} |`,
      ...body.map((row) => `| ${pad(row).join(" | ")} |`),
    ];
    return `${lines.join("\n")}\n\n`;
  }
  if (BLOCK.has(tag)) return blocks(el, depth);

  // An inline element sitting where a block was expected, e.g. a bare <span>.
  const text = inline(el).trim();
  return text ? `${text}\n\n` : "";
}

function isList(el: Element): boolean {
  return el.tagName === "UL" || el.tagName === "OL";
}

function blocks(el: Element, depth = 0): string {
  return children(el, (child) => block(child, depth)).join("");
}

/** The summary's HTML as Markdown, with a title heading when one is given. */
export function summaryToMarkdown(html: string, title?: string | null): string {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const body = blocks(parsed.body)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  const heading = title?.trim();
  // Only when the document does not already open with one: the prompt usually writes
  // its own <h1>, and two titles in a row reads as a mistake.
  if (heading && !/^#\s/.test(body)) return `# ${heading}\n\n${body}\n`;
  return `${body}\n`;
}

/** A filename that survives every filesystem this runs on. */
export function markdownFilename(title: string | null, id: string): string {
  const base = (title ?? id).trim().replace(/[\\/:*?"<>|]/g, "-").replace(/\s+/g, " ");
  return `${base.slice(0, 80) || id}.md`;
}
