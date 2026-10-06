import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, reason, type Transcription } from "../api";
import { useI18n } from "../i18n";
import { formatDurationShort, formatShortDate } from "../lib/format";
import {
  DOWNLOAD_FORMATS,
  LIST_KEY,
  canCancel,
  canRetry,
  clientLabel,
  isDone,
  stateLabel,
} from "../lib/transcriptions";
import Menu from "./Menu";
import { confirmDialog } from "./ConfirmDialog";
import { toast } from "./Toaster";
import TranscriptView from "./TranscriptView";

const FORMAT_NAMES: Record<string, string> = {
  txt: "TXT",
  srt: "SRT",
  vtt: "VTT",
  json: "JSON",
  md: "Markdown",
};

/** A plain link, clicked: the page's cookie goes with it, so this works with the switch off. */
function download(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

export default function TranscriptionRow({ job }: { job: Transcription }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: LIST_KEY, exact: true });
  const failed = (error: unknown) => toast({ title: reason(error), tone: "danger" });
  const cancel = useMutation({ mutationFn: () => api.cancelTranscription(job.id), onSuccess: refresh, onError: failed });
  const retry = useMutation({ mutationFn: () => api.retryTranscription(job.id), onSuccess: refresh, onError: failed });
  const remove = useMutation({ mutationFn: () => api.deleteTranscription(job.id), onSuccess: refresh, onError: failed });

  const meta = [
    formatDurationShort(job.duration_s),
    job.language ? job.language.toUpperCase() : null,
    clientLabel(job, t),
    formatShortDate(job.created_at, locale),
  ].filter(Boolean);
  const active = job.state === "running";

  return (
    <li data-testid="transcription-row" data-id={job.id} data-state={job.state} className="rounded-lg bg-raised shadow-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <div className="min-w-[12rem] flex-1">
          <p className="truncate text-sm font-medium" dir="auto" title={job.source_name}>
            {job.source_name}
          </p>
          <p className="mt-0.5 text-xs text-tertiary">{meta.join(" · ")}</p>
        </div>
        <div className="min-w-[10rem] text-xs">
          <p
            data-testid="transcription-state"
            className={job.state === "failed" ? "text-danger" : job.state === "done" ? "text-secondary" : "text-primary"}
          >
            {stateLabel(job, t)}
          </p>
          {job.state === "failed" && job.error && (
            <p data-testid="transcription-error" className="mt-0.5 max-w-xs truncate text-tertiary" title={job.error}>
              {job.error}
            </p>
          )}
          {active && (
            <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-2">
              <div className="h-full bg-accent" style={{ width: `${Math.round(job.progress * 100)}%` }} />
            </div>
          )}
        </div>
        <div className="flex items-center gap-1">
          {isDone(job) && (
            <>
              <button
                type="button"
                data-testid="transcription-view"
                aria-expanded={open}
                onClick={() => setOpen(!open)}
                className="h-7 rounded-md px-2 text-xs hover:bg-a-200"
              >
                {open ? t("transcriptions.hide") : t("transcriptions.view")}
              </button>
              <Menu
                label={t("transcriptions.download")}
                testid="transcription-download"
                triggerClassName="h-7 rounded-md px-2 text-xs hover:bg-a-200"
                trigger={<>{t("transcriptions.download")}</>}
                items={DOWNLOAD_FORMATS.map((format) => ({
                  id: format,
                  label: FORMAT_NAMES[format],
                  run: () => download(api.transcriptionDownloadUrl(job.id, format)),
                }))}
              />
              <button
                type="button"
                data-testid="transcription-copy"
                onClick={() => {
                  void api
                    .transcriptionText(job.id)
                    .then((text) => navigator.clipboard?.writeText(text))
                    .then(() => setCopied(true), failed);
                }}
                className="h-7 rounded-md px-2 text-xs hover:bg-a-200"
              >
                {copied ? t("transcriptions.copied") : t("transcriptions.copy")}
              </button>
            </>
          )}
          {canCancel(job) && (
            <button
              type="button"
              data-testid="transcription-cancel"
              onClick={() => cancel.mutate()}
              className="h-7 rounded-md px-2 text-xs hover:bg-a-200"
            >
              {t("transcriptions.cancel")}
            </button>
          )}
          {canRetry(job) && (
            <button
              type="button"
              data-testid="transcription-retry"
              onClick={() => retry.mutate()}
              className="h-7 rounded-md px-2 text-xs hover:bg-a-200"
            >
              {t("transcriptions.retry")}
            </button>
          )}
          <Menu
            label={t("transcriptions.more")}
            testid="transcription-menu"
            items={[
              {
                id: "delete",
                label: t("transcriptions.delete"),
                danger: true,
                run: () => {
                  void confirmDialog({
                    title: t("transcriptions.deleteTitle"),
                    body: t("transcriptions.deleteBody").replace("{name}", job.source_name),
                    confirm: t("transcriptions.delete"),
                    cancel: t("transcriptions.keep"),
                  }).then((yes) => yes && remove.mutate());
                },
              },
            ]}
          />
        </div>
      </div>
      {open && isDone(job) && (
        <div className="border-t border-line">
          <TranscriptView job={job} />
        </div>
      )}
    </li>
  );
}
