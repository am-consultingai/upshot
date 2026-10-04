/**
 * Who a transcript speaker slot is, what colour they are, and how long they talked.
 *
 * The recorder knows two things as hardware facts — the microphone (`ME`) and
 * everything the machine played (`THEM`) — and diarisation splits each into its
 * voices: `ME_1`, `ME_2`… when several people spoke into the microphone, `THEM_1`,
 * `THEM_2`… when several were on the call. One voice keeps `ME` or `THEM`. None of
 * those is a name. A name comes from one of two places, in this order:
 *
 *  1. the user, who named the slot on this meeting (`speaker_names`), or
 *  2. the invitation, when it leaves no doubt: an undivided `THEM` track in a meeting
 *     whose calendar lists exactly one other person. That one is marked as a guess,
 *     because a colleague who joined uninvited would make it wrong.
 *
 * Anything else stays a slot — "Them", "Speaker 2", "Mic 2" — rather than an invented
 * person. Every slot can be named, `ME` included.
 */
import type { MessageKey } from "../locales/en";

export interface Segment {
  start: number;
  end?: number;
  speaker: string;
  text: string;
}

export interface Speaker {
  slot: string;
  name: string;
  /** Named from the invitation rather than by the user. */
  guessed: boolean;
  /** From the microphone: `ME`, or one of `ME_1`, `ME_2`… */
  mine: boolean;
  seconds: number;
  share: number;
  colour: string;
  initials: string;
}

/** Five hues that stay apart in both themes and from the accent; defined in tokens.css. */
const COLOURS = ["var(--speaker-1)", "var(--speaker-2)", "var(--speaker-3)", "var(--speaker-4)", "var(--speaker-5)"];

/**
 * A person's colour, from their name — so Dana is the same violet on her avatar in
 * the rail, on her line in the transcript and on the action items she owns. You are
 * always the first hue; everyone else hashes onto the other four.
 */
export function personColour(name: string, mine = false): string {
  if (mine) return COLOURS[0];
  let hash = 0;
  for (const char of name.toLowerCase()) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return COLOURS[1 + (hash % (COLOURS.length - 1))];
}

export function initials(name: string): string {
  const parts = name.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export interface SpeakerWords {
  you: string;
  them: string;
  /** "Speaker {n}", for `THEM_n`. */
  speaker: string;
  /** "Mic {n}", for `ME_n`. */
  mic: string;
}

/** The microphone's slots: `ME`, or `ME_n` when several people spoke into it. */
export function isMicSlot(slot: string): boolean {
  return slot === "ME" || /^ME_\d+$/.test(slot);
}

export function nameFor(
  slot: string,
  named: Record<string, string>,
  participants: string[],
  words: SpeakerWords,
): { name: string; guessed: boolean } {
  if (named[slot]) return { name: named[slot], guessed: false };
  if (slot === "ME") return { name: words.you, guessed: false };
  if (slot === "THEM" && participants.length === 1) return { name: participants[0], guessed: true };
  if (slot === "THEM") return { name: words.them, guessed: false };
  const mic = slot.match(/^ME_(\d+)$/)?.[1];
  if (mic) return { name: words.mic.replace("{n}", mic), guessed: false };
  const number = slot.match(/^THEM_(\d+)$/)?.[1];
  return { name: number ? words.speaker.replace("{n}", number) : slot, guessed: false };
}

/**
 * Who said a search hit, from its slot and the name the user gave it on its meeting.
 * No invitation guess here: search does not carry the meeting's participants.
 */
export function hitSpeaker(
  hit: { speaker: string; speaker_name?: string | null },
  t: (key: MessageKey) => string,
): string {
  const named = hit.speaker_name ? { [hit.speaker]: hit.speaker_name } : {};
  const words = {
    you: t("meeting.you"),
    them: t("meeting.themSaid"),
    speaker: t("meeting.speakerN"),
    mic: t("meeting.micN"),
  };
  return nameFor(hit.speaker, named, [], words).name;
}

/**
 * Seconds per slot, from each segment's own start and end.
 *
 * Real talk time now that segments carry an end. Where one does not (an old
 * transcript), the gap to the next segment stands in for it, capped at thirty
 * seconds so a long silence is not counted as somebody speaking.
 */
export function talkSeconds(segments: Segment[]): Map<string, number> {
  const out = new Map<string, number>();
  segments.forEach((segment, index) => {
    const next = segments[index + 1]?.start;
    const end =
      segment.end !== undefined && segment.end > segment.start
        ? segment.end
        : Math.min(next ?? segment.start + 4, segment.start + 30);
    out.set(segment.speaker, (out.get(segment.speaker) ?? 0) + Math.max(0, end - segment.start));
  });
  return out;
}

/** Everyone who spoke: the microphone's voices first, then by how much they said. */
export function speakers(
  segments: Segment[],
  named: Record<string, string>,
  participants: string[],
  words: SpeakerWords,
): Speaker[] {
  const seconds = talkSeconds(segments);
  const total = [...seconds.values()].reduce((sum, value) => sum + value, 0) || 1;
  const slots = [...seconds.keys()].sort((a, b) => {
    if (isMicSlot(a) !== isMicSlot(b)) return isMicSlot(a) ? -1 : 1;
    return (seconds.get(b) ?? 0) - (seconds.get(a) ?? 0);
  });
  const taken = new Set<string>();
  return slots.map((slot) => {
    const { name, guessed } = nameFor(slot, named, participants, words);
    // By name, so a person keeps their colour across screens — moved to the next free
    // hue only when two people in the same meeting would otherwise share one.
    let colour = personColour(name, slot === "ME");
    for (let step = 1; taken.has(colour) && step < COLOURS.length; step += 1) {
      colour = COLOURS[1 + (COLOURS.indexOf(colour) % (COLOURS.length - 1))];
    }
    taken.add(colour);
    return {
      slot,
      name,
      guessed,
      mine: isMicSlot(slot),
      seconds: seconds.get(slot) ?? 0,
      share: (seconds.get(slot) ?? 0) / total,
      colour,
      initials: initials(name),
    };
  });
}

/** `(00:00:04)` — hours always shown, so the column of timestamps is one width. */
export function stamp(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(Math.floor(whole / 3600))}:${pad(Math.floor((whole % 3600) / 60))}:${pad(whole % 60)}`;
}
