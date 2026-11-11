import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason, type ErrorNotice } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import Button from "./Button";
import { toast } from "./Toaster";

const FAILED_WHILE: Record<string, MessageKey> = {
  transcribe: "meeting.failedWhileTranscribe",
  assemble: "meeting.failedWhileAssemble",
  summarize: "meeting.failedWhileSummarize",
  render: "meeting.failedWhileRender",
  deliver: "meeting.failedWhileDeliver",
};

/**
 * The error window (owner, 2026-11-10): a meeting that failed for a reason Upshot does not
 * recognise is shown in a window of its own, not only in the meeting's red box. With
 * crash reports on, the report has gone and the window says so; otherwise it shows the
 * report as it would be sent, and sends it only if the user presses Send. One at a time,
 * oldest first; each stays until closed, across restarts.
 */
export default function ErrorNoticeHost() {
  const notices = useQuery({
    queryKey: ["error-notices"],
    queryFn: api.errorNotices,
    // Server events refetch it as a job fails; this only catches one that slipped by.
    refetchInterval: 60_000,
  });
  const notice = notices.data?.items[0];
  if (!notice) return null;
  return <ErrorNoticeDialog key={notice.id} notice={notice} />;
}

function ErrorNoticeDialog({ notice }: { notice: ErrorNotice }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showReport, setShowReport] = useState(false);
  const first = useRef<HTMLButtonElement | null>(null);
  const close = useMutation({
    mutationFn: (send: boolean) => api.closeErrorNotice(notice.id, send),
    onSuccess: (answer, send) => {
      if (send && answer.report.status === "sent") toast({ title: t("errorNotice.thanks") });
      void queryClient.invalidateQueries({ queryKey: ["error-notices"] });
    },
    onError: (error) => toast({ title: reason(error), tone: "danger" }),
  });
  const report = notice.report;
  const asks = report.status === "ask";

  useEffect(() => {
    first.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      // Escape is "not now" for a report that asks, and Close otherwise: never a send.
      if (event.key === "Escape" && !close.isPending) close.mutate(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);

  return createPortal(
    <div className="fixed inset-0 z-[80] grid place-items-center bg-scrim-soft px-4 backdrop-blur-xs">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="error-notice-title"
        data-testid="error-notice"
        className="ma-dialog w-full max-w-md rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <h2 id="error-notice-title" className="text-md font-semibold tracking-snug">
          {notice.title
            ? t("errorNotice.title").replace("{title}", notice.title)
            : t("meeting.failedLead")}
        </h2>
        <p className="mt-1 text-sm">{t(FAILED_WHILE[notice.stage] ?? "meeting.failedWhileWorking")}</p>
        <p className="mt-1 text-sm text-secondary">{t("meeting.failedSafe")}</p>
        <details data-testid="error-notice-detail" className="mt-2">
          <summary className="cursor-pointer text-xs text-tertiary">{t("meeting.failedDetail")}</summary>
          <p dir="ltr" className="mt-1 max-h-32 overflow-auto font-mono text-2xs text-tertiary">
            {notice.message}
          </p>
        </details>

        {report.status === "sent" && (
          <p data-testid="error-notice-sent" className="mt-3 text-sm text-secondary">
            {report.reference
              ? t("errorNotice.sent").replace("{reference}", report.reference)
              : t("errorNotice.sentToday")}
          </p>
        )}
        {asks && (
          <div className="mt-3 text-sm">
            <p>{t("errorNotice.ask")}</p>
            <button
              type="button"
              data-testid="error-notice-preview"
              className="mt-1 text-xs underline"
              onClick={() => setShowReport((open) => !open)}
            >
              {t("errorNotice.preview")}
            </button>
            {showReport && (
              <pre
                data-testid="error-notice-report"
                dir="ltr"
                className="mt-1 max-h-60 overflow-auto rounded bg-surface-2 p-2 font-mono text-2xs"
              >
                {JSON.stringify(report.event, null, 2)}
              </pre>
            )}
          </div>
        )}

        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button
            data-testid="error-notice-open"
            variant="ghost"
            onClick={() => {
              navigate(`/m/${notice.meeting_id}`);
              close.mutate(false);
            }}
          >
            {t("errorNotice.open")}
          </Button>
          {asks ? (
            <>
              <Button
                ref={first}
                data-testid="error-notice-dont-send"
                variant="secondary"
                disabled={close.isPending}
                onClick={() => close.mutate(false)}
              >
                {t("errorNotice.dontSend")}
              </Button>
              <Button
                data-testid="error-notice-send"
                variant="primary"
                busy={close.isPending && close.variables === true}
                disabled={close.isPending}
                onClick={() => close.mutate(true)}
              >
                {t("errorNotice.send")}
              </Button>
            </>
          ) : (
            <Button
              ref={first}
              data-testid="error-notice-close"
              variant="primary"
              disabled={close.isPending}
              onClick={() => close.mutate(false)}
            >
              {t("errorNotice.close")}
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
