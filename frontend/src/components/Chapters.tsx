import type { Chapter } from "../api";
import { useI18n } from "../i18n";
import { stamp } from "../lib/speakers";
import Tooltip from "./Tooltip";

/**
 * The transcript's chapters, as one row above it that scrolls sideways: each is a
 * title and its time range, and pressing one plays from there.
 */
export default function Chapters({ chapters, onSeek }: { chapters: Chapter[]; onSeek: (seconds: number) => void }) {
  const { t } = useI18n();
  if (chapters.length === 0) return null;
  return (
    <section data-testid="meeting-chapters" className="mb-4">
      <Tooltip label={t("meeting.jumpTo")} hint={t("help.chapters")}>
        <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("meeting.jumpTo")}</h2>
      </Tooltip>
      <ul className="-mx-2 mt-1.5 flex gap-1 overflow-x-auto pb-1 [scrollbar-width:thin]">
        {chapters.map((chapter) => (
          <li key={`${chapter.start_ms}-${chapter.title}`} className="shrink-0">
            <button
              type="button"
              data-testid="chapter"
              data-start-ms={chapter.start_ms}
              onClick={() => onSeek(chapter.start_ms / 1000)}
              className="block max-w-56 rounded-md px-2 py-1.5 text-start hover:bg-a-200 active:bg-a-300"
            >
              <span className="block truncate text-sm">{chapter.title}</span>
              <span className="mt-px block font-mono text-2xs text-tertiary tabular-nums">
                {stamp(chapter.start_ms / 1000).replace(/^00:/, "")}
                {chapter.end_ms !== null && ` – ${stamp(chapter.end_ms / 1000).replace(/^00:/, "")}`}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
