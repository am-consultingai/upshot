import { useEffect, useRef, useState } from "react";
import { useMutation, useMutationState, useQueryClient } from "@tanstack/react-query";
import { api, reason, type MeetingDetail } from "../api";
import { useI18n } from "../i18n";
import type { Speaker } from "../lib/speakers";
import { toast } from "./Toaster";

/** Collapses runs of whitespace, as the server does; empty means "no name". */
function cleanName(name: string): string {
  return name.split(/\s+/).filter(Boolean).join(" ");
}

function renameKey(meetingId: string) {
  return ["rename-speaker", meetingId];
}

/**
 * The meeting's speaker names with every rename still on its way to the server laid
 * over them. The page polls the meeting, and a poll answered before a rename is stored
 * would otherwise put the old name back on screen until the rename settled.
 */
export function useSpeakerNames(meetingId: string, saved: Record<string, string>): Record<string, string> {
  const pending = useMutationState({
    filters: { mutationKey: renameKey(meetingId), status: "pending" },
    select: (mutation) => mutation.state.variables as { slot: string; name: string } | undefined,
  });
  if (pending.length === 0) return saved;
  const names = { ...saved };
  for (const rename of pending) {
    if (!rename) continue;
    const clean = cleanName(rename.name);
    if (clean) names[rename.slot] = clean;
    else delete names[rename.slot];
  }
  return names;
}

/**
 * Name a speaker slot on this meeting, everywhere at once.
 *
 * The header and every transcript turn read their names from the meeting's
 * `speaker_names`, so the cached meeting is patched before the server answers: a
 * rename made on one line shows on every other line and in the header in the same
 * frame. An empty name clears the slot back to "Speaker 2", as the server does.
 */
export function useSpeakerRename(meetingId: string) {
  const queryClient = useQueryClient();
  const key = ["meeting", meetingId];
  const mutationKey = renameKey(meetingId);
  /** One slot of the cached meeting, set or cleared, the others left as they are. */
  const setSlot = (slot: string, name: string | undefined) =>
    queryClient.setQueryData(key, (old: MeetingDetail | undefined) => {
      if (!old) return old;
      const names = { ...(old.speaker_names ?? {}) };
      if (name) names[slot] = name;
      else delete names[slot];
      return { ...old, speaker_names: names };
    });
  return useMutation({
    mutationKey,
    mutationFn: ({ slot, name }: { slot: string; name: string }) =>
      api.patchMeeting(meetingId, { speaker_names: { [slot]: name || null } }),
    onMutate: async ({ slot, name }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<MeetingDetail>(key);
      setSlot(slot, cleanName(name) || undefined);
      return { previous };
    },
    /*
     * The server's answer is the meeting as stored, so its name for this slot goes in
     * now: a poll answered while the rename was on its way may have put the old one
     * back, and the pending overlay stops covering the slot once this settles.
     */
    onSuccess: (saved, { slot }) => setSlot(slot, saved.speaker_names?.[slot]),
    // Only the failed slot goes back: another rename still in flight keeps its name.
    onError: (error, { slot }, context) => {
      setSlot(slot, context?.previous?.speaker_names?.[slot]);
      toast({ title: reason(error), tone: "danger" });
    },
    onSettled: async () => {
      // Search hits carry the name a slot was given.
      void queryClient.invalidateQueries({ queryKey: ["search"] });
      // Another rename still in flight: drop any poll on its way, which would not have
      // that rename yet; the last one to settle refetches.
      if (queryClient.isMutating({ mutationKey }) > 1) {
        await queryClient.cancelQueries({ queryKey: key });
        return;
      }
      // Awaited, so the rename stays pending — and on screen — until the fresh copy lands.
      await queryClient.invalidateQueries({ queryKey: key });
    },
  });
}

/**
 * A speaker's name that turns into a field on double-click.
 *
 * Enter or leaving the field saves, Escape puts the name back — the same as the
 * meeting title. In the header the name is also a button, so the keyboard and screen
 * readers reach it: pressing it (Enter, Space, a screen reader's click) or F2 opens the
 * field, and focus comes back to it after Enter or Escape. A mouse click does not —
 * that stays a double-click. In the transcript it is not a button, or every turn would
 * be a tab stop.
 */
export function SpeakerName({
  person,
  onRename,
  focusable = false,
  className = "",
}: {
  person: Speaker;
  onRename: (slot: string, name: string) => void;
  focusable?: boolean;
  className?: string;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<string | null>(null);
  // Escape has to beat the blur that removing the field fires (see the title rename).
  const cancelled = useRef(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  // Closed with Enter or Escape: the keyboard's place goes back to the name, not the page.
  const refocus = useRef(false);
  useEffect(() => {
    if (draft !== null || !refocus.current) return;
    refocus.current = false;
    trigger.current?.focus();
  }, [draft]);

  const commit = () => {
    const next = (draft ?? "").trim();
    setDraft(null);
    if (cancelled.current) {
      cancelled.current = false;
      return;
    }
    if (next !== person.name) onRename(person.slot, next);
  };

  if (draft !== null) {
    return (
      <input
        data-testid="speaker-name-input"
        data-slot={person.slot}
        autoFocus
        value={draft}
        aria-label={t("meeting.renameSpeaker")}
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onClick={(event) => event.stopPropagation()}
        maxLength={80}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            // Kept from the name button that takes focus back: it would press it and reopen.
            event.preventDefault();
            refocus.current = focusable;
            event.currentTarget.blur();
          }
          if (event.key === "Escape") {
            // The page's Escape hands focus to the meeting list; this one only cancels.
            event.preventDefault();
            event.stopPropagation();
            refocus.current = focusable;
            // The blur is ours, so the cancel path runs and clears the flag.
            cancelled.current = true;
            event.currentTarget.blur();
          }
        }}
        className="no-focus-ring w-full max-w-60 min-w-0 rounded-xs bg-surface-2 px-1 font-normal shadow-[0_0_0_1px_var(--accent)] outline-none"
      />
    );
  }

  const label = (
    <>
      <bdi>{person.name}</bdi>
      {person.guessed && <span className="text-tertiary">?</span>}
    </>
  );
  const title = person.guessed ? t("meeting.speakerGuessed") : t("meeting.renameSpeakerHint");
  const open = () => setDraft(person.name);
  return focusable ? (
    <button
      ref={trigger}
      type="button"
      data-testid="speaker-name"
      data-slot={person.slot}
      title={title}
      onDoubleClick={open}
      // detail 0: a press from the keyboard or assistive technology, not a mouse click.
      onClick={(event) => {
        if (event.detail === 0) open();
      }}
      onKeyDown={(event) => {
        if (event.key === "F2") {
          event.preventDefault();
          open();
        }
      }}
      className={`min-w-0 truncate rounded-xs text-start ${className}`}
    >
      {label}
    </button>
  ) : (
    <span data-testid="speaker-name" data-slot={person.slot} title={title} onDoubleClick={open} className={className}>
      {label}
    </span>
  );
}

/**
 * Who spoke, in the meeting header: one chip per voice, in their colour, with how
 * much of the meeting they talked.
 *
 * The row takes at most the room of three chips and scrolls sideways past that, so a
 * meeting of eight people costs the header no more height than a meeting of two. The
 * scrollbar is hidden — on Windows it would add a row under the chips — and the wheel
 * scrolls the row sideways instead; the chip cut off at the edge says there is more.
 */
export default function MeetingSpeakers({
  people,
  onRename,
  className = "",
}: {
  people: Speaker[];
  onRename: (slot: string, name: string) => void;
  className?: string;
}) {
  const { t } = useI18n();
  const row = useRef<HTMLUListElement | null>(null);
  // A native listener: React's wheel handler is passive and cannot keep the page still.
  useEffect(() => {
    const node = row.current;
    if (!node) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (node.scrollWidth <= node.clientWidth || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      event.preventDefault();
      node.scrollLeft += getComputedStyle(node).direction === "rtl" ? -event.deltaY : event.deltaY;
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [people.length]);
  if (people.length === 0) return null;
  return (
    <ul
      ref={row}
      data-testid="meeting-speakers"
      aria-label={t("meeting.speakers")}
      className={`flex max-w-[27rem] min-w-0 gap-1.5 overflow-x-auto [scrollbar-width:none] ${className}`}
    >
      {people.map((person) => (
        <li
          key={person.slot}
          data-testid="talk-share"
          data-track={person.mine ? "me" : "them"}
          data-slot={person.slot}
          title={`${person.name} · ${Math.round(person.share * 100)}%`}
          className="inline-flex h-6 w-[8.5rem] shrink-0 items-center gap-1.5 rounded-full ps-2.5 pe-2 text-xs text-secondary shadow-[var(--shadow-ring)]"
        >
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full" style={{ background: person.colour }} />
          <SpeakerName
            person={person}
            focusable
            onRename={onRename}
            className="flex-1 text-primary"
          />
          <span className="font-mono tabular-nums text-tertiary">{Math.round(person.share * 100)}%</span>
        </li>
      ))}
    </ul>
  );
}
