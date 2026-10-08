import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { isMicSlot, stamp, type Segment, type Speaker } from "../lib/speakers";

/** Where each match is: which segment, and where in its text. */
interface Match {
  segment: number;
  offset: number;
}

export function findMatches(segments: Segment[], needle: string): Match[] {
  const out: Match[] = [];
  if (!needle) return out;
  segments.forEach((segment, index) => {
    const text = segment.text.toLowerCase();
    for (let at = text.indexOf(needle); at !== -1; at = text.indexOf(needle, at + needle.length)) {
      out.push({ segment: index, offset: at });
    }
  });
  return out;
}

/**
 * The transcript, as speaker blocks.
 *
 * A block per turn of the conversation: the speaker's initials and name, then their
 * lines, each opening with a tinted timestamp that plays the recording from there. The
 * name is dropped on a consecutive turn by the same person — repeating it every two
 * lines is what turns a transcript into a stutter.
 *
 * Find works the way it does in a document rather than as a filter: every match is
 * marked, "2 of 4" says where you are, Enter and Shift+Enter step through them, and the
 * turn holding the current match carries a rule on its leading edge. Filtering the
 * transcript down to matching lines, which is what this did before, threw away the
 * context a match is usually wanted for.
 */
const Transcript = forwardRef<
  HTMLInputElement,
  {
    segments: Segment[];
    people: Speaker[];
    dir: "ltr" | "rtl";
    /** The segment being spoken, from the player. */
    speaking: number;
    /** Keep the line being spoken in view: on while playing, until the reader scrolls away. */
    following?: boolean;
    /** The reader scrolled the transcript themselves. */
    onUnfollow?: () => void;
    onSeek: (seconds: number) => void;
  }
>(function Transcript({ segments, people, dir, speaking, following = false, onUnfollow, onSeek }, findRef) {
  const { t } = useI18n();
  const [find, setFind] = useState("");
  const [current, setCurrent] = useState(0);
  const list = useRef<HTMLOListElement | null>(null);
  const needle = find.trim().toLowerCase();
  const matches = useMemo(() => findMatches(segments, needle), [segments, needle]);
  const bySlot = new Map(people.map((person) => [person.slot, person]));

  const unfollow = useRef(onUnfollow);
  unfollow.current = onUnfollow;

  useEffect(() => setCurrent(0), [needle]);
  useEffect(() => {
    if (matches.length === 0) return;
    // Finding is reading somewhere else: playback stops pulling the page back.
    unfollow.current?.();
    list.current
      ?.querySelector(`[data-match-index="${current}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [current, matches.length]);

  /*
   * The transcript follows playback, as Descript's and Otter's do: each new line
   * scrolls into the middle of the view as it starts.
   *
   * Only "nearest" would be calmer, but it parks the line at the bottom edge where the
   * next one is always off screen. And it stops the moment the reader takes the scroll
   * themselves — a page that drags you back mid-sentence is worse than one that does
   * not follow at all. A click on a line, play, J or K turn it back on (the page owns
   * that state). Only gestures count as taking over: wheel, touch, the scrollbar and
   * the scrolling keys; the smooth scroll this does itself fires none of them.
   */
  useEffect(() => {
    if (!following || speaking < 0) return;
    list.current
      ?.querySelector(`[data-segment-index="${speaking}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [following, speaking]);
  useEffect(() => {
    if (!following) return undefined;
    let scroller: HTMLElement | null = list.current?.parentElement ?? null;
    while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) {
      scroller = scroller.parentElement;
    }
    if (!scroller) return undefined;
    const box = scroller;
    const taken = () => unfollow.current?.();
    const onPointer = (event: PointerEvent) => {
      // A press on the scroller itself, not on anything in it, is a press on its scrollbar.
      if (event.target === box) taken();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) taken();
    };
    box.addEventListener("wheel", taken, { passive: true });
    box.addEventListener("touchmove", taken, { passive: true });
    box.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      box.removeEventListener("wheel", taken);
      box.removeEventListener("touchmove", taken);
      box.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [following]);

  const step = (by: number) => {
    if (matches.length === 0) return;
    setCurrent((at) => (at + by + matches.length) % matches.length);
  };
  const activeSegment = matches[current]?.segment ?? -1;
  let matchIndex = -1;

  return (
    <>
      <div
        data-testid="transcript-find"
        className="sticky top-0 z-10 -mx-1 mb-5 flex h-8 items-center gap-2 rounded-md bg-surface-1 px-2.5 shadow-[var(--shadow-ring-subtle)] focus-within:shadow-[0_0_0_1px_var(--accent)]"
      >
        <svg viewBox="0 0 16 16" className="size-3.5 shrink-0 fill-none stroke-current stroke-[1.6] text-tertiary">
          <path d="M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z" />
        </svg>
        <input
          ref={findRef}
          data-testid="transcript-find-input"
          value={find}
          onChange={(event) => setFind(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              step(event.shiftKey ? -1 : 1);
            }
            if (event.key === "Escape") setFind("");
          }}
          placeholder={t("meeting.findInTranscript")}
          className="no-focus-ring min-w-0 flex-1 bg-transparent text-sm outline-none"
        />
        {needle && (
          <span data-testid="transcript-find-count" data-total={matches.length} className="font-mono text-2xs text-tertiary tabular-nums">
            {matches.length === 0
              ? t("meeting.noMatches")
              : t("meeting.matchOf").replace("{n}", String(current + 1)).replace("{total}", String(matches.length))}
          </span>
        )}
        {matches.length > 1 && (
          <span className="flex">
            <button
              type="button"
              data-testid="transcript-find-prev"
              aria-label={t("meeting.previousMatch")}
              onClick={() => step(-1)}
              className="grid size-5 place-items-center rounded-xs text-tertiary hover:bg-a-200 hover:text-primary"
            >
              <svg viewBox="0 0 16 16" className="size-3 fill-none stroke-current stroke-[1.6]">
                <path d="M4 9.5 8 5.5l4 4" />
              </svg>
            </button>
            <button
              type="button"
              data-testid="transcript-find-next"
              aria-label={t("meeting.nextMatch")}
              onClick={() => step(1)}
              className="grid size-5 place-items-center rounded-xs text-tertiary hover:bg-a-200 hover:text-primary"
            >
              <svg viewBox="0 0 16 16" className="size-3 fill-none stroke-current stroke-[1.6]">
                <path d="M4 6.5 8 10.5l4-4" />
              </svg>
            </button>
          </span>
        )}
        {/* The short key; Ctrl+F still works, and is what people try first. */}
        <kbd className="kbd-hint shrink-0 rounded-2xs px-1 font-mono text-3xs text-tertiary shadow-[var(--shadow-ring-subtle)]">
          /
        </kbd>
      </div>

      <ol ref={list} data-testid="transcript" dir={dir} className="pb-6">
        {segments.map((segment, index) => {
          const person = bySlot.get(segment.speaker);
          const continues = index > 0 && segments[index - 1].speaker === segment.speaker;
          const isSpeaking = index === speaking;
          const holdsCurrent = index === activeSegment;
          const parts: React.ReactNode[] = [];
          if (needle) {
            const lower = segment.text.toLowerCase();
            let from = 0;
            for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, from)) {
              matchIndex += 1;
              if (at > from) parts.push(segment.text.slice(from, at));
              parts.push(
                <mark
                  key={at}
                  data-testid="transcript-match"
                  data-match-index={matchIndex}
                  data-current={matchIndex === current ? "true" : undefined}
                  className={`font-medium text-accent underline decoration-from-font underline-offset-3 ${
                    matchIndex === current ? "rounded-xs bg-accent-quiet" : "bg-transparent"
                  }`}
                >
                  {segment.text.slice(at, at + needle.length)}
                </mark>,
              );
              from = at + needle.length;
            }
            parts.push(segment.text.slice(from));
          }
          return (
            <li
              key={index}
              data-testid="transcript-block"
              data-segment-index={index}
              data-current-match={holdsCurrent ? "true" : undefined}
              className={`${continues ? "pt-3" : "pt-5 first:pt-0"} ${
                holdsCurrent || isSpeaking ? "-ms-3.5 ps-3 shadow-[inset_2px_0_0_0_var(--accent)]" : ""
              }`}
            >
              {!continues && (
                <span data-testid="turn-speaker" className="mb-1 flex items-center gap-2 text-sm font-semibold tracking-snug">
                  <span
                    aria-hidden="true"
                    className="grid size-4 place-items-center rounded-full text-[7.5px] font-bold text-on-speaker"
                    style={{ background: person?.colour ?? "var(--border-strong)" }}
                  >
                    {person?.initials ?? "?"}
                  </span>
                  <bdi>{person?.name ?? segment.speaker}</bdi>
                </span>
              )}
              <p
                data-speaking={isSpeaking ? "true" : undefined}
                className="-mx-2 rounded-md px-2 text-md leading-[1.68] hover:bg-a-100"
              >
                {/*
                 * The timestamp is a tinted anchor at the head of the paragraph: it
                 * seeks, and it gives the eye a fixed start-edge column to scan down.
                 */}
                <button
                  type="button"
                  data-testid="transcript-turn"
                  data-at-ms={Math.round(segment.start * 1000)}
                  data-track={isMicSlot(segment.speaker) ? "me" : "them"}
                  data-speaking={isSpeaking ? "true" : undefined}
                  onClick={() => onSeek(segment.start)}
                  className="me-1.5 align-baseline font-mono text-xs text-accent underline decoration-from-font underline-offset-3 hover:brightness-110"
                >
                  ({stamp(segment.start)})
                </button>
                {needle ? parts : segment.text}
              </p>
            </li>
          );
        })}
      </ol>
    </>
  );
});

export default Transcript;
