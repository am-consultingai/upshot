import { stamp } from "./speakers";

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

/**
 * Every point the summary took from one turn (`data-at-ms`, D94), followed by a quiet
 * `(00:12:34)` control that plays the recording from there: the transcript's own
 * timestamp in the transcript's own style, at the logical end of the point.
 *
 * Added to the markup the page shows, never to the file: Copy and the Markdown export
 * read the summary as written, so a pasted email carries no dead timestamps. The control
 * stays out of a text selection and out of print for the same reason. The page's click
 * handler reads `data-cite` and seeks. A summary with no citations comes back unchanged.
 */
export const CITE_CLASS =
  "summary-cite ms-1.5 align-baseline font-mono text-xs text-accent underline decoration-from-font underline-offset-3 hover:brightness-110 select-none print:hidden";

export function withCitations(html: string, label: (time: string) => string): string {
  if (!/data-at-ms/i.test(html)) return html;
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  let changed = false;
  for (const point of doc.body.querySelectorAll<HTMLElement>("li[data-at-ms], p[data-at-ms]")) {
    const raw = point.getAttribute("data-at-ms")?.trim() ?? "";
    if (!/^\d+$/.test(raw)) continue;
    const time = stamp(Number(raw) / 1000);
    const button = doc.createElement("button");
    button.type = "button";
    button.className = CITE_CLASS;
    button.dataset.cite = raw;
    button.dataset.testid = "summary-cite";
    // Digits and brackets read left to right inside a Hebrew line too.
    button.dir = "ltr";
    button.setAttribute("aria-label", label(time));
    button.textContent = `(${time})`;
    // A point with a sub-list under it: the moment belongs to its own words, before the list.
    const nested = [...point.children].find((child) => child.tagName === "UL" || child.tagName === "OL");
    point.insertBefore(button, nested ?? null);
    changed = true;
  }
  return changed ? doc.body.innerHTML : html;
}

/**
 * The moment a click in the summary asks to play, in seconds, or `null`. Only a control
 * withCitations made counts (a `button.summary-cite`), and only a whole number of ms: the
 * summary's own markup can't steer the player, and a bad value never seeks to NaN.
 */
export function citedSeconds(target: EventTarget | null): number | null {
  if (!(target instanceof Element)) return null;
  const cite = target.closest<HTMLElement>("button.summary-cite[data-cite]");
  const ms = cite?.dataset.cite ?? "";
  return /^\d{1,9}$/.test(ms) ? Number(ms) / 1000 : null;
}
