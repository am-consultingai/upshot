/**
 * A summary as the page shows it: opening with its lead sentence, not a label.
 *
 * The prompt now asks for a document that opens with one plain paragraph stating the
 * outcome. Every summary written before that opens with `<h2>Overview</h2>` (or the
 * meeting's own title as an `<h1>`), which is a heading that says nothing the page
 * does not already say — and it is what stopped the lead-sentence style from ever
 * applying, since that rule is `> p:first-child`.
 *
 * So the display drops exactly those: a first heading that is a generic label or a
 * repeat of the title, and only when a paragraph follows it for the lead to be. The
 * file on disk is not touched, a heading that says something ("Decisions") is never
 * dropped, and the Markdown export still carries the document as written.
 */
const GENERIC = /^(overview|summary|meeting summary|tl;?\s?dr|executive summary|סקירה(\s+כללית)?|סיכום(\s+הפגישה)?|תקציר)[:.]?$/i;

export function leadFirst(html: string, title: string | null = null): string {
  if (!html.trim()) return html;
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const body = doc.body;
  // A summary the renderer wrapped keeps its wrapper; look inside it.
  const root =
    body.children.length === 1 && body.firstElementChild?.tagName === "DIV" ? body.firstElementChild : body;
  const first = root.firstElementChild;
  if (!first || !/^H[1-3]$/.test(first.tagName)) return html;
  const text = (first.textContent ?? "").trim();
  const repeatsTitle = title !== null && text.toLowerCase() === title.trim().toLowerCase();
  if (!GENERIC.test(text) && !repeatsTitle) return html;
  if (first.nextElementSibling?.tagName !== "P") return html;
  first.remove();
  // Unwrapped again, so `.summary-prose > p:first-child` can reach the lead.
  return root === body ? body.innerHTML.trim() : root.innerHTML.trim();
}
