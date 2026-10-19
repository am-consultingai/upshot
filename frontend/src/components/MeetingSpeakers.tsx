import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, reason, type MeetingDetail } from "../api";
import { useI18n } from "../i18n";
import type { Speaker } from "../lib/speakers";
import { toast } from "./Toaster";

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
  return useMutation({
    mutationFn: ({ slot, name }: { slot: string; name: string }) =>
      api.patchMeeting(meetingId, { speaker_names: { [slot]: name || null } }),
    onMutate: async ({ slot, name }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<MeetingDetail>(key);
      queryClient.setQueryData(key, (old: MeetingDetail | undefined) => {
        if (!old) return old;
        const names = { ...(old.speaker_names ?? {}) };
        const clean = name.split(/\s+/).filter(Boolean).join(" ");
        if (clean) names[slot] = clean;
        else delete names[slot];
        return { ...old, speaker_names: names };
      });
      return { previous };
    },
    onError: (error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
      toast({ title: reason(error), tone: "danger" });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
      // Search hits carry the name a slot was given.
      void queryClient.invalidateQueries({ queryKey: ["search"] });
    },
  });
}

/**
 * A speaker's name that turns into a field on double-click.
 *
 * Enter or leaving the field saves, Escape puts the name back — the same as the
 * meeting title. In the header the name is also a button, so the keyboard reaches it
 * (Enter or F2 opens the field); in the transcript it is not, or every turn would be
 * a tab stop.
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
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            cancelled.current = true;
            setDraft(null);
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
  const title = person.guessed ? t("meeting.speakerGuessed") : t("meeting.renameSpeaker");
  const open = () => setDraft(person.name);
  return focusable ? (
    <button
      type="button"
      data-testid="speaker-name"
      data-slot={person.slot}
      title={title}
      onDoubleClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === "F2") {
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
 * meeting of eight people costs the header no more height than a meeting of two.
 */
export default function MeetingSpeakers({
  meetingId,
  people,
  className = "",
}: {
  meetingId: string;
  people: Speaker[];
  className?: string;
}) {
  const { t } = useI18n();
  const rename = useSpeakerRename(meetingId);
  if (people.length === 0) return null;
  return (
    <ul
      data-testid="meeting-speakers"
      aria-label={t("meeting.railSpeakers")}
      className={`flex max-w-[27rem] min-w-0 gap-1.5 overflow-x-auto [scrollbar-width:thin] ${className}`}
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
            onRename={(slot, name) => rename.mutate({ slot, name })}
            className="flex-1 text-primary"
          />
          <span className="font-mono tabular-nums text-tertiary">{Math.round(person.share * 100)}%</span>
        </li>
      ))}
    </ul>
  );
}
