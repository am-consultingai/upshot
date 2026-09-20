import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

/**
 * The invitation behind a recording: what the organizer wrote, the links in it, and the
 * files they attached.
 *
 * Read from Google when this card is shown, and not stored: the invitation lives in the
 * calendar, which is where it is maintained, so an agenda edited after the meeting reads
 * correctly here. Offline it simply says so — everything else on the page still works.
 */
export default function InviteCard({ meetingId }: { meetingId: string }) {
  const { t } = useI18n();
  const invite = useQuery({
    queryKey: ["meeting-invite", meetingId],
    queryFn: () => api.meetingInvite(meetingId),
    // The event can change between visits, and nothing here is cached on our side.
    staleTime: 30_000,
    retry: false,
  });

  const data = invite.data;
  if (!data || (!data.available && !data.reason)) return null;

  if (!data.available) {
    return (
      <p data-testid="invite-unavailable" className="mb-4 text-xs text-tertiary">
        {t("invite.unavailable")} {data.reason}
      </p>
    );
  }

  const event = data.invite!;
  const hasBody =
    event.agenda || event.attachments.length > 0 || event.links.length > 0 || event.location;
  if (!hasBody) return null;

  return (
    <section
      data-testid="invite-card"
      className="mb-4 rounded-lg bg-raised px-4 py-3 text-sm shadow-sm"
    >
      <header className="mb-1 flex flex-wrap items-baseline gap-2">
        <h2 className="text-xs font-medium text-tertiary">{t("invite.heading")}</h2>
        {event.html_link && (
          <a
            data-testid="invite-open"
            href={event.html_link}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-secondary underline"
          >
            {t("invite.openInCalendar")}
          </a>
        )}
      </header>

      {event.location && (
        <p className="text-secondary" data-testid="invite-location">
          {event.location}
        </p>
      )}

      {event.agenda && (
        // The organizer's own text, with its line breaks kept and its links already
        // written out beside their labels. Rendered as text, never as their HTML.
        <p
          data-testid="invite-agenda"
          className="mt-1 max-w-prose whitespace-pre-wrap text-secondary"
        >
          {event.agenda}
        </p>
      )}

      {event.attachments.length > 0 && (
        <div className="mt-2">
          <h3 className="text-xs font-medium text-tertiary">{t("invite.attachments")}</h3>
          <ul data-testid="invite-attachments" className="mt-0.5 space-y-0.5">
            {event.attachments.map((file) => (
              <li key={file.url}>
                <a
                  href={file.url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                  data-testid="invite-attachment"
                >
                  {file.title || file.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {event.links.length > 0 && (
        <div className="mt-2">
          <h3 className="text-xs font-medium text-tertiary">{t("invite.links")}</h3>
          <ul data-testid="invite-links" className="mt-0.5 space-y-0.5">
            {event.links.map((href) => (
              <li key={href} className="truncate">
                <a href={href} target="_blank" rel="noreferrer" className="underline">
                  {href}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
