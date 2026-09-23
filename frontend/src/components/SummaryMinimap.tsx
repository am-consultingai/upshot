import { useEffect, useState } from "react";
import { useI18n } from "../i18n";

interface Section {
  title: string;
  node: HTMLElement;
}

/**
 * A minimap of the summary's sections, on the document's edge.
 *
 * One short rule per heading, the section you are in drawn longer and darker —
 * Notion's and Craft's table-of-contents strip. Hovering the strip names the
 * sections; clicking one scrolls to it. It costs 24px and no reading width, which is
 * why it is a strip rather than an outline panel: a summary is short enough that the
 * outline is a convenience, not a navigation system.
 *
 * Built from the headings the model actually wrote, after the HTML has rendered — a
 * summary with fewer than two sections has nothing to map and draws nothing.
 */
export default function SummaryMinimap({
  root,
  scroller,
  version,
}: {
  /** The rendered summary. */
  root: HTMLElement | null;
  /** What scrolls: the reading column. */
  scroller: HTMLElement | null;
  /** Changes when the summary does, so the headings are read again. */
  version: string;
}) {
  const { t } = useI18n();
  const [sections, setSections] = useState<Section[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!root) return;
    const found = [...root.querySelectorAll<HTMLElement>("h1, h2, h3")]
      .filter((node) => node.tagName !== "H3" || root.querySelectorAll("h2").length === 0)
      .map((node) => ({ title: node.textContent?.trim() ?? "", node }))
      .filter((section) => section.title);
    setSections(found);
  }, [root, version]);

  useEffect(() => {
    if (!scroller || sections.length === 0) return;
    const onScroll = () => {
      // At the foot of the page the last section is the one being read, even when it
      // is too short to reach the top.
      if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) {
        setActive(sections.length - 1);
        return;
      }
      const top = scroller.getBoundingClientRect().top + 96;
      let current = 0;
      sections.forEach((section, index) => {
        if (section.node.getBoundingClientRect().top <= top) current = index;
      });
      setActive(current);
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [scroller, sections]);

  if (sections.length < 2) return null;
  return (
    <nav
      data-testid="summary-minimap"
      aria-label={t("meeting.sections")}
      onPointerEnter={() => setOpen(true)}
      onPointerLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      className="sticky top-8 hidden h-0 w-6 shrink-0 lg:block"
    >
      <ul className="relative flex flex-col items-end gap-2 pt-1">
        {sections.map((section, index) => (
          <li key={index} className="relative flex w-full justify-end">
            <button
              type="button"
              data-testid="minimap-section"
              data-active={index === active ? "true" : undefined}
              aria-label={section.title}
              aria-current={index === active ? "location" : undefined}
              onClick={() => {
                setActive(index);
                section.node.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
              className="group flex h-2 w-6 items-center justify-end"
            >
              <span
                className={`block h-[1.5px] rounded-full transition-all ${
                  index === active ? "w-5 bg-secondary" : "w-3 bg-line-strong group-hover:bg-tertiary"
                }`}
              />
            </button>
            {open && (
              <span
                className={`pointer-events-none absolute top-1/2 me-8 -translate-y-1/2 whitespace-nowrap rounded-xs bg-raised px-2 py-0.5 text-xs shadow-[var(--shadow-ring),var(--shadow-sm)] ${
                  index === active ? "text-primary" : "text-secondary"
                }`}
                style={{ insetInlineEnd: 0 }}
              >
                {section.title}
              </span>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
