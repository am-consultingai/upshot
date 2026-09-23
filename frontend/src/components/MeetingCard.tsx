import { NavLink, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import type { Meeting } from "../api";
import { formatDuration, formatDurationShort, formatClock, formatElapsed } from "../lib/format";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import Menu, { useContextMenu, type MenuItem } from "./Menu";

const ETA_PER_STATE: Record<string, number> = {
  TRANSCRIBING: 8 * 60,
  SUMMARIZING: 90,
  SUMMARIZED: 20,
  RECORDED: 10 * 60,
};

export function etaSeconds(meeting: Meeting): number | null {
  const base = ETA_PER_STATE[meeting.state];
  if (base === undefined) return null;
  const factor = meeting.state === "TRANSCRIBING" ? (meeting.duration_s ?? 600) / 600 : 1;
  return Math.round(base * factor);
}

/** A meeting that has finished its pipeline and needs nothing from anyone. */
const SETTLED = new Set(["RENDERED", "DELIVERED"]);

/** Colour only where it means something; everything settled is a quiet dot. */
const DOT: Record<string, string> = {
  RECORDING: "bg-danger shadow-[0_0_0_3px_color-mix(in_oklab,var(--base),var(--danger)_20%)]",
  FAILED: "bg-danger",
  RENDERED: "bg-success",
  DELIVERED: "bg-success",
};

/** What failed, as the row says it: "summary failed" rather than "failed". */
const FAILED_AT: Record<string, MessageKey> = {
  transcribe: "sidebar.failedTranscript",
  assemble: "sidebar.failedTranscript",
  summarize: "sidebar.failedSummary",
  render: "sidebar.failedSummary",
  deliver: "sidebar.failedDelivery",
};

/**
 * One meeting in the sidebar's list.
 *
 * Two lines in about 220px: the title, and beneath it the facts you choose by, in
 * one mono run — `09:30 · 42m · 3 items`. The previous card put the open count in a
 * trailing badge and left the length out below a minute, so a row said `09:30` and a
 * bare `3` and the reader had to know what the 3 counted.
 *
 * Selection is a mark on the leading edge plus a faint fill; hover is a fill alone.
 * They are different kinds of signal, so a hovered row next to the selected one can
 * never be mistaken for it — Attio's rule, and the mock's.
 *
 * Everything a row can do lives in one menu, reached by the `⋯` that appears in a
 * reserved slot on hover or focus, or by right-clicking the row. Delete used to be a
 * trash can revealed in the same place, which gave the one irreversible action the
 * only affordance on the row.
 */
export default function MeetingCard({
  meeting,
  onStop,
  onDelete,
  onResummarize,
  selected = false,
}: {
  meeting: Meeting;
  onStop?: () => void;
  onDelete?: () => void;
  onResummarize?: () => void;
  /** Whether this is the meeting the detail pane is showing. */
  selected?: boolean;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [now, setNow] = useState(() => Date.now());
  const recording = meeting.state === "RECORDING";

  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [recording]);

  const eta = etaSeconds(meeting);
  /*
   * What this meeting still owes: "3 items" while any are open, "done" once every one
   * is ticked. Zero open is not the same news as none recorded — a meeting whose
   * commitments are all discharged has earned the word, and one that never made any
   * has nothing to say.
   */
  const total = meeting.actions_total ?? 0;
  const openItems = meeting.actions_open ?? 0;
  const cleared = total > 0 && openItems === 0;
  const length = formatDurationShort(meeting.duration_s);

  const items: MenuItem[] = [
    { id: "open", label: t("sidebar.open"), run: () => navigate(`/m/${meeting.id}`) },
    { id: "rename", label: t("meeting.rename"), run: () => navigate(`/m/${meeting.id}?rename=1`) },
    ...(recording && onStop ? [{ id: "stop", label: t("timeline.stop"), run: onStop }] : []),
    ...(!recording && onResummarize
      ? [{ id: "resummarize", label: t("meeting.resummarize"), run: onResummarize }]
      : []),
    ...(!recording && onDelete
      ? [{ id: "delete", label: t("meeting.deleteEllipsis"), danger: true, separated: true, run: onDelete }]
      : []),
  ];
  const context = useContextMenu(items);

  const dot = <span className="opacity-50">{" · "}</span>;

  return (
    <article
      data-testid="meeting-card"
      data-meeting-id={meeting.id}
      data-state={meeting.state}
      role="option"
      aria-selected={selected}
      onContextMenu={context.onContextMenu}
      className={`ma-row group relative rounded-md ${
        selected ? "bg-a-100 hover:bg-a-200" : "hover:bg-a-200 active:bg-a-300"
      }`}
    >
      <NavLink
        to={`/m/${meeting.id}`}
        data-testid="meeting-link"
        className="flex items-start gap-2 rounded-md py-1.5 ps-2 pe-8"
      >
        <span
          data-testid="meeting-state-dot"
          data-state={meeting.state}
          className={`mt-1.5 size-1.5 shrink-0 rounded-full ${DOT[meeting.state] ?? "bg-border-strong"}`}
        />
        <span className="min-w-0 flex-1">
          <span
            data-testid="meeting-name"
            className={`block truncate text-sm ${recording ? "text-danger" : "text-primary"}`}
          >
            {meeting.title ?? meeting.id}
          </span>
          <span
            className={`mt-px block truncate font-mono text-2xs tabular-nums ${
              recording ? "text-danger" : "text-tertiary"
            }`}
          >
            {recording ? (
              <bdi data-testid="elapsed">
                {formatClock(meeting.started_at)}
                {dot}
                {t("sidebar.recording")} {formatElapsed(meeting.started_at, now)}
              </bdi>
            ) : (
              <>
                <bdi data-testid="meeting-clock">{formatClock(meeting.started_at)}</bdi>
                {/* A failed row has one thing to say, and the length is not it. */}
                {length && meeting.state !== "FAILED" && (
                  <>
                    {dot}
                    <bdi data-testid="meeting-duration">{length}</bdi>
                  </>
                )}
                {meeting.state === "FAILED" ? (
                  <>
                    {dot}
                    <bdi data-testid="meeting-state">
                      {t(FAILED_AT[meeting.failed_stage ?? ""] ?? "sidebar.failed")}
                    </bdi>
                  </>
                ) : (
                  /*
                   * A meeting still working says so in words. The dot alone separates
                   * finished from failed by colour, but not "transcribing" from "done"
                   * — and the one you want to know about is the one still moving.
                   */
                  !SETTLED.has(meeting.state) && (
                    <>
                      {dot}
                      <bdi data-testid="meeting-state">
                        {t(`state.${meeting.state}` as MessageKey).toLowerCase()}
                      </bdi>
                    </>
                  )
                )}
                {eta !== null && (
                  <>
                    {dot}
                    <bdi data-testid="eta">~{formatDuration(eta, t)}</bdi>
                  </>
                )}
                {total > 0 && (
                  <>
                    {dot}
                    <bdi
                      data-testid="meeting-actions-badge"
                      data-open={openItems}
                      data-total={total}
                      data-cleared={cleared ? "true" : "false"}
                      title={
                        cleared
                          ? t("timeline.actionsCleared")
                          : t("timeline.actionsOpen").replace("{n}", String(openItems))
                      }
                      className={cleared ? "text-success" : undefined}
                    >
                      {cleared
                        ? t("sidebar.done")
                        : t(openItems === 1 ? "sidebar.item" : "sidebar.items").replace(
                            "{n}",
                            String(openItems),
                          )}
                    </bdi>
                  </>
                )}
              </>
            )}
          </span>
        </span>
      </NavLink>

      {/* A reserved slot: the `⋯` never pushes the title, it only appears over its own space. */}
      <span className="ma-slot absolute end-1.5 top-1.5" data-pinned={undefined}>
        <Menu label={t("meeting.more")} items={items} testid="meeting-menu" small />
      </span>
      {context.menu}
    </article>
  );
}
