import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Chapter, type MeetingDetail, type RelatedReason } from "../api";
import { useI18n } from "../i18n";
import { formatShortDate } from "../lib/format";
import { speakers, stamp, type Segment, type Speaker } from "../lib/speakers";
import AskMeeting from "./AskMeeting";
import Tooltip from "./Tooltip";
import type { MessageKey } from "../locales/en";

/**
 * The rail beside a meeting.
 *
 * The reading column is capped at 672px because prose stops being readable much past
 * 70 characters a line. What was wrong was leaving the rest of the pane empty, which
 * is a shape no application in this category ships: Linear puts properties here,
 * Circleback the people and its related notes, Otter a chat and an outline.
 *
 * Beside the summary it holds who was in the room and how much each of them said,
 * the meetings this one connects to and why, and a box to ask the meeting a question.
 * Beside the transcript it holds the transcript's chapters and the same speakers,
 * because what you want while reading a transcript is to move around in it.
 */
export default function MeetingRail({
  meeting,
  segments,
  tab,
  onSeek,
}: {
  meeting: MeetingDetail;
  segments: Segment[];
  tab: "summary" | "transcript";
  onSeek: (seconds: number) => void;
}) {
  const { t, locale } = useI18n();
  const participants = meeting.calendar?.participants ?? [];
  const words = { you: t("meeting.you"), them: t("meeting.themSaid"), speaker: t("meeting.speakerN") };
  const people = speakers(segments, meeting.speaker_names ?? {}, participants, words);
  // Invited, but not a voice the transcript can name: listed without a bar.
  const heard = new Set(people.map((person) => person.name.toLowerCase()));
  const silent = participants.filter((name) => !heard.has(name.toLowerCase()));

  const related = useQuery({
    queryKey: ["related", meeting.id],
    queryFn: () => api.related(meeting.id),
    enabled: tab === "summary",
    staleTime: 60_000,
  });

  return (
    <aside
      data-testid="meeting-rail"
      data-tab={tab}
      className="hidden w-[19rem] shrink-0 flex-col gap-6.5 overflow-y-auto border-s border-line-subtle px-5 pt-6.5 pb-10 xl:flex"
    >
      {tab === "transcript" && (meeting.chapters?.length ?? 0) > 0 && (
        <Chapters chapters={meeting.chapters ?? []} onSeek={onSeek} />
      )}

      {(people.length > 0 || silent.length > 0) && (
        <section data-testid="rail-people">
          <Tooltip
            label={tab === "summary" ? t("meeting.railPeople") : t("meeting.railSpeakers")}
            hint={t("help.speakers")}
          >
            <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">
              {tab === "summary" ? t("meeting.railPeople") : t("meeting.railSpeakers")}
            </h2>
          </Tooltip>
          <ul className="mt-3 grid gap-3">
            {people.map((person) => (
              <SpeakerRow key={person.slot} meeting={meeting} person={person} />
            ))}
            {tab === "summary" &&
              silent.map((name) => (
                <li key={name} data-testid="rail-invitee" className="flex items-center gap-2 text-sm text-tertiary">
                  <span
                    aria-hidden="true"
                    className="grid size-5.5 shrink-0 place-items-center rounded-full bg-surface-3 text-[9px] font-semibold"
                  >
                    {name.slice(0, 2).toUpperCase()}
                  </span>
                  <bdi className="truncate">{name}</bdi>
                </li>
              ))}
          </ul>
        </section>
      )}

      {tab === "summary" && (related.data?.related.length ?? 0) > 0 && (
        <section data-testid="rail-related-section">
          <Tooltip label={t("meeting.railRelated")} hint={t("help.related")}>
            <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("meeting.railRelated")}</h2>
          </Tooltip>
          <ul className="mt-2 grid gap-px">
            {(related.data?.related ?? []).map((other) => (
              <li key={other.id}>
                <Link
                  to={`/m/${other.id}`}
                  data-testid="rail-related"
                  className="group -mx-2 block rounded-md px-2 py-1.5 hover:bg-a-200 active:bg-a-300"
                >
                  <span className="block truncate text-sm transition-transform duration-200 group-hover:translate-x-0.5 rtl:group-hover:-translate-x-0.5">
                    {other.title ?? other.id}
                  </span>
                  <span className="mt-px block truncate font-mono text-2xs text-tertiary tabular-nums">
                    {formatShortDate(other.started_at, locale, false)}
                    {mostTelling(other.reasons).map((reason, index) => (
                      <span key={index} data-testid="rail-related-reason" data-code={reason.code}>
                        {" · "}
                        {reasonText(reason, t)}
                      </span>
                    ))}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tab === "summary" && segments.length > 0 && <AskMeeting meetingId={meeting.id} onSeek={onSeek} />}
    </aside>
  );
}

/**
 * The one reason worth the line, most specific first: a shared commitment says more
 * than a shared room, which says more than a shared word, which says more than a
 * recurring title.
 */
const TELLING: RelatedReason["code"][] = ["shared_actions", "same_people", "mentions", "same_series"];

function mostTelling(reasons: RelatedReason[]): RelatedReason[] {
  return [...reasons].sort((a, b) => TELLING.indexOf(a.code) - TELLING.indexOf(b.code)).slice(0, 1);
}

/** Why another meeting is here, in the rail's words: "shares 2 action items". */
export function reasonText(reason: RelatedReason, t: (key: MessageKey) => string): string {
  switch (reason.code) {
    case "shared_actions":
      return t(reason.count === 1 ? "related.sharesOne" : "related.shares").replace("{n}", String(reason.count));
    case "same_people":
      return t("related.samePeople");
    case "same_series":
      return t("related.sameSeries");
    case "mentions":
      return t("related.mentions").replace("{term}", reason.term);
    default:
      return "";
  }
}

/** One voice: avatar, name, share, and a bar in their colour. */
function SpeakerRow({ meeting, person }: { meeting: MeetingDetail; person: Speaker }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const rename = useMutation({
    mutationFn: (name: string) =>
      api.patchMeeting(meeting.id, { speaker_names: { [person.slot]: name || null } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meeting", meeting.id] });
      void queryClient.invalidateQueries({ queryKey: ["search"] });
    },
  });
  const commit = () => {
    const next = (draft ?? "").trim();
    setDraft(null);
    if (next !== person.name) rename.mutate(next);
  };

  return (
    <li data-testid="talk-share" data-track={person.mine ? "me" : "them"} data-slot={person.slot}>
      <div className="flex items-center gap-2 text-sm">
        <span
          aria-hidden="true"
          className="grid size-5.5 shrink-0 place-items-center rounded-full text-[9px] font-semibold text-on-speaker"
          style={{ background: person.colour }}
        >
          {person.initials}
        </span>
        {draft === null ? (
          <button
            type="button"
            data-testid="speaker-name"
            disabled={person.mine}
            onClick={() => setDraft(person.name)}
            title={person.mine ? undefined : person.guessed ? t("meeting.speakerGuessed") : t("meeting.renameSpeaker")}
            className="min-w-0 flex-1 truncate rounded-xs text-start enabled:hover:underline enabled:hover:decoration-dotted enabled:hover:underline-offset-4"
          >
            <bdi>{rename.isPending ? rename.variables : person.name}</bdi>
            {person.guessed && <span className="text-tertiary">?</span>}
          </button>
        ) : (
          <input
            data-testid="speaker-name-input"
            autoFocus
            value={draft}
            aria-label={t("meeting.renameSpeaker")}
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") setDraft(null);
            }}
            className="no-focus-ring min-w-0 flex-1 rounded-xs bg-surface-2 px-1 text-sm shadow-[0_0_0_1px_var(--accent)] outline-none"
          />
        )}
        <span className="font-mono text-2xs text-tertiary tabular-nums">{Math.round(person.share * 100)}%</span>
      </div>
      <div className="mt-1.5 h-[3px] overflow-hidden rounded-sm bg-surface-3">
        <i
          className="block h-full rounded-sm"
          style={{ width: `${person.share * 100}%`, background: person.colour, opacity: 0.75 }}
        />
      </div>
    </li>
  );
}

function Chapters({ chapters, onSeek }: { chapters: Chapter[]; onSeek: (seconds: number) => void }) {
  const { t } = useI18n();
  return (
    <section data-testid="rail-chapters">
      <Tooltip label={t("meeting.jumpTo")} hint={t("help.chapters")}>
        <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("meeting.jumpTo")}</h2>
      </Tooltip>
      <ul className="mt-2 grid gap-px">
        {chapters.map((chapter) => (
          <li key={`${chapter.start_ms}-${chapter.title}`}>
            <button
              type="button"
              data-testid="rail-chapter"
              data-start-ms={chapter.start_ms}
              onClick={() => onSeek(chapter.start_ms / 1000)}
              className="-mx-2 block w-[calc(100%+1rem)] rounded-md px-2 py-1.5 text-start hover:bg-a-200 active:bg-a-300"
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
