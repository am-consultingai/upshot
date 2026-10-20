import { Link, Navigate, useParams } from "react-router-dom";
import { useI18n } from "../i18n";
import { formatShortDate } from "../lib/format";
import { useTour } from "./TourProvider";

/**
 * A sample meeting's page, drawn from the tour's sample and nothing else.
 *
 * The real meeting page asks the server for everything it shows, and a sample has nothing
 * on the server to ask for: it lives in the page's memory (./sample.ts). So this draws the
 * same header, action items and summary from the sample, with no controls that would act
 * on it. Once the tour is over the address leads home.
 */
export default function SampleMeeting() {
  const { id = "" } = useParams();
  const { t, locale } = useI18n();
  const { sample } = useTour();
  const meeting = sample?.meetings.find((item) => item.id === id);
  if (!sample || !meeting) return <Navigate to="/" replace />;
  const { detail } = sample;
  const due = (day: string) => formatShortDate(`${day}T12:00:00`, locale, false);

  return (
    <section data-testid="meeting-page" data-meeting-id={id} data-tour-sample className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 flex-none items-center gap-2 border-b border-line-subtle px-5">
        <Link to="/" className="text-sm text-tertiary hover:text-primary">
          {t("nav.timeline")}
        </Link>
        <span className="text-sm text-tertiary opacity-50">/</span>
        <span className="truncate text-sm text-secondary">
          <bdi>{formatShortDate(meeting.started_at, locale)}</bdi>
        </span>
      </div>
      <header className="flex-none border-b border-line-subtle px-8 pt-3 pb-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <h1 data-testid="meeting-title" className="display min-w-0 truncate text-[1.5rem] leading-tight">
            {meeting.title}
          </h1>
          <SampleTag />
        </div>
        <div className="mt-2 flex w-max gap-0.5 rounded-md bg-surface-3 p-0.5">
          <span className="grid h-control-sm place-items-center rounded-sm bg-raised px-3.5 text-sm text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]">
            {t("meeting.summary")}
          </span>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-5">
        <p data-testid="tour-sample-note" className="mb-4 inline-block rounded-md bg-accent-quiet px-2.5 py-1.5 text-xs text-accent">
          {t("tour.sampleNote")}
        </p>
        <div data-tour="sample-summary">
          <section
            data-testid="meeting-actions"
            className="mb-7 rounded-xl bg-surface-1 px-1.5 pt-2.5 pb-1.5 shadow-[var(--shadow-ring-subtle)]"
          >
            <h2 className="mb-1 px-2 text-sm font-semibold tracking-snug">{t("meeting.actionItems")}</h2>
            <ul>
              {detail.actions.map((action) => (
                <li key={action.what} className="flex items-start gap-2.5 rounded-md px-2.5 py-1.5">
                  <span aria-hidden="true" className="mt-0.5 size-4.5 shrink-0 rounded-full shadow-[inset_0_0_0_1.5px_var(--border-strong)]" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-md leading-snug">{action.what}</span>
                    <span className="mt-0.5 block text-sm text-tertiary">
                      {action.who} · <span className="tabular-nums">{due(action.due)}</span>
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </section>
          <div data-testid="summary-html" className="summary-prose mb-12">
            <h2>{t("tour.sampleDecisions")}</h2>
            <ul>
              {detail.decisions.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <h2>{t("tour.sampleQuestions")}</h2>
            <ul>
              {detail.questions.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

/** "Sample", beside anything the tour made up. */
export function SampleTag() {
  const { t } = useI18n();
  return (
    <span
      data-testid="tour-sample-tag"
      className="shrink-0 rounded-full bg-accent-quiet px-1.5 text-3xs leading-4 font-medium text-accent"
    >
      {t("tour.sampleTag")}
    </span>
  );
}
