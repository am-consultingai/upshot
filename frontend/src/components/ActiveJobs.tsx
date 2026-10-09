import { Link } from "react-router-dom";
import Button from "./Button";
import { confirmDialog } from "./ConfirmDialog";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, reason, type ActiveJob } from "../api";
import { useI18n } from "../i18n";
import { ACTIVE_KEY, clientName, jobStatus, timeLeft, useActiveJobs } from "../lib/activeJobs";
import { LIST_KEY } from "../lib/transcriptions";
import { SettingGroup } from "./SettingRow";
import { toast } from "./Toaster";

function Row({ job }: { job: ActiveJob }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => api.cancelTranscription(job.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ACTIVE_KEY, exact: true });
      void queryClient.invalidateQueries({ queryKey: LIST_KEY, exact: true });
    },
    onError: (error) => toast({ title: reason(error), tone: "danger" }),
  });
  const left = job.state === "running" ? timeLeft(job.eta_s, t) : null;
  const to = job.kind === "meeting" ? `/m/${job.id}` : "/transcriptions";
  return (
    <li
      data-testid="active-job"
      data-kind={job.kind}
      data-id={job.id}
      data-state={job.state}
      className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg bg-raised px-4 py-3 shadow-sm"
    >
      <div className="min-w-[14rem] flex-1">
        <Link
          to={to}
          data-testid="active-job-link"
          dir="auto"
          className="block truncate text-sm font-medium hover:underline"
          title={job.title ?? undefined}
        >
          {job.title ?? job.id}
        </Link>
        <p className="mt-0.5 text-xs text-tertiary">
          <span data-testid="active-job-client">{clientName(job, t)}</span>
          {" · "}
          <span data-testid="active-job-status" className="text-secondary">
            {jobStatus(job, t, locale)}
          </span>
          {left && (
            <>
              {" · "}
              <span data-testid="active-job-eta">{left}</span>
            </>
          )}
        </p>
        {job.state === "running" && job.progress !== null && (
          <div
            className="mt-1.5 h-1 w-full max-w-xs overflow-hidden rounded-full bg-surface-2"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(job.progress * 100)}
          >
            <div className="h-full bg-accent" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
        )}
      </div>
      {job.cancellable && (
        <Button
          variant="ghost"
          data-testid="active-job-cancel"
          busy={cancel.isPending}
          onClick={() => {
            // A waiting job has nothing to lose; a running one asks first.
            if (job.state !== "running") {
              cancel.mutate();
              return;
            }
            void confirmDialog({
              title: t("activeJobs.cancelTitle"),
              body: t("activeJobs.cancelBody").replace("{title}", job.title ?? job.id),
              confirm: t("activeJobs.cancelConfirm"),
              cancel: t("activeJobs.keepRunning"),
            }).then((yes) => yes && cancel.mutate());
          }}
        >
          {t("transcriptions.cancel")}
        </Button>
      )}
    </li>
  );
}

/**
 * Settings → Transcription opens with what the one worker is doing and what waits for
 * it: meetings and files, from this app, from programs through the API, and from Claude
 * through the MCP bridge, in the order they will run. Finished work is not here; it is
 * on the meeting or on the Transcriptions page. Live through the event stream.
 */
export default function ActiveJobs() {
  const { t } = useI18n();
  const active = useActiveJobs();
  const jobs = active.data?.jobs ?? [];
  return (
    <SettingGroup title={t("activeJobs.title")}>
      {jobs.length === 0 ? (
        <p data-testid="active-jobs-empty" className="rounded-lg bg-raised px-4 py-3 text-sm text-secondary shadow-sm">
          {active.isSuccess ? t("activeJobs.empty") : " "}
        </p>
      ) : (
        <ul data-testid="active-jobs" className="space-y-1">
          {jobs.map((job) => (
            <Row key={`${job.kind}:${job.id}:${job.stage ?? ""}`} job={job} />
          ))}
        </ul>
      )}
    </SettingGroup>
  );
}
