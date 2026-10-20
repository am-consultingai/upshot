import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type RelatedReason } from "../api";
import { useI18n } from "../i18n";
import { formatShortDate } from "../lib/format";
import AskMeeting from "./AskMeeting";
import Tooltip from "./Tooltip";
import type { MessageKey } from "../locales/en";

/**
 * What follows a summary: the meetings this one connects to and why, and a box to
 * ask the meeting a question.
 *
 * These lived in a rail beside the meeting, which took a column of the page for
 * itself and, below the widest windows, left that column empty. Speakers moved into
 * the header, the chapters above the transcript (Chapters.tsx), and these two below
 * the summary, where they are read once the summary has been — and the reading
 * column gets the width back.
 */
export default function MeetingRelated({
  meetingId,
  transcribed,
  onSeek,
}: {
  meetingId: string;
  transcribed: boolean;
  onSeek: (seconds: number) => void;
}) {
  const { t, locale } = useI18n();
  const related = useQuery({
    queryKey: ["related", meetingId],
    queryFn: () => api.related(meetingId),
    staleTime: 60_000,
  });
  const others = related.data?.related ?? [];
  if (others.length === 0 && !transcribed) return null;

  return (
    <div
      data-testid="meeting-related"
      // Two columns only when there are two things: alone, the box takes the width.
      className={`mb-12 grid gap-8 border-t border-line-subtle pt-6 ${
        others.length > 0 && transcribed ? "lg:grid-cols-2" : ""
      }`}
    >
      {others.length > 0 && (
        <section data-testid="related-meetings">
          <Tooltip label={t("meeting.relatedMeetings")} hint={t("help.related")}>
            <h2 className="w-max text-2xs uppercase tracking-wide text-tertiary">{t("meeting.relatedMeetings")}</h2>
          </Tooltip>
          <ul className="mt-2 grid gap-px">
            {others.map((other) => (
              <li key={other.id}>
                <Link
                  to={`/m/${other.id}`}
                  data-testid="related-meeting"
                  className="group -mx-2 block rounded-md px-2 py-1.5 hover:bg-a-200 active:bg-a-300"
                >
                  <span className="block truncate text-sm transition-transform duration-200 group-hover:translate-x-0.5 rtl:group-hover:-translate-x-0.5">
                    {other.title ?? other.id}
                  </span>
                  <span className="mt-px block truncate font-mono text-2xs text-tertiary tabular-nums">
                    {formatShortDate(other.started_at, locale, false)}
                    {mostTelling(other.reasons).map((reason, index) => (
                      <span key={index} data-testid="related-reason" data-code={reason.code}>
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

      {transcribed && <AskMeeting meetingId={meetingId} onSeek={onSeek} />}
    </div>
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

/** Why another meeting is here, in the list's words: "shares 2 action items". */
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
