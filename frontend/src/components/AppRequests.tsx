import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import TranscriptionDropzone from "./TranscriptionDropzone";
import TranscriptionRow from "./TranscriptionRow";
import { toast } from "./Toaster";
import AiActivity from "./AiActivity";
import { Loading, Skeleton } from "./Skeleton";
import EmptyState, { EMPTY_BUTTON, EMPTY_ICON } from "./EmptyState";
import { LIST_KEY, loadOptions, saveOptions, upload, type UploadOptions } from "../lib/transcriptions";

interface Uploading {
  key: number;
  name: string;
  fraction: number;
}

let nextKey = 1;

const TABS = ["transcriptions", "activity"] as const;
type Tab = (typeof TABS)[number];

/**
 * Settings → App requests (D98): what the user's AI apps asked of Upshot, and the files
 * transcribed for them or dropped here. `?tab=activity` opens the second tab; the old
 * address, /transcriptions, leads here.
 */
export default function AppRequests() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  // Local state, not the URL: Settings keeps its section in the hash, which a search
  // parameter change would drop.
  const [tab, setTab] = useState<Tab>(params.get("tab") === "activity" ? "activity" : "transcriptions");
  return (
    <section data-testid="app-requests">
      <div data-testid="ai-apps-tabs" role="tablist" className="mb-6 flex w-max gap-0.5 rounded-md bg-surface-3 p-0.5">
        {TABS.map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            id={`ai-apps-tab-${value}`}
            aria-controls="ai-apps-panel"
            tabIndex={tab === value ? 0 : -1}
            data-testid={`ai-apps-tab-${value}`}
            aria-selected={tab === value}
            onKeyDown={(event) => {
              // Arrow keys move between the tabs, as a tab list should.
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
              const next = TABS[(TABS.indexOf(value) + 1) % TABS.length];
              setTab(next);
              document.getElementById(`ai-apps-tab-${next}`)?.focus();
            }}
            onClick={() => setTab(value)}
            className={`flex h-control-sm items-center rounded-sm px-3 text-sm ${
              tab === value
                ? "bg-raised text-primary shadow-[var(--shadow-sm),var(--shadow-ring-subtle),var(--shadow-edge)]"
                : "text-secondary hover:bg-a-200 hover:text-primary"
            }`}
          >
            {t(value === "activity" ? "aiApps.tabActivity" : "aiApps.tabTranscriptions")}
          </button>
        ))}
      </div>
      <div id="ai-apps-panel" role="tabpanel" aria-labelledby={`ai-apps-tab-${tab}`}>
        {tab === "activity" ? <AiActivity /> : <TranscriptionsTab />}
      </div>
    </section>
  );
}

/**
 * Transcriptions of files, apart from meetings (D86, R4): what is added here, through
 * the API or by Claude never appears in the library, search or summaries. Rows update
 * live from `transcription` events, through the list's own cache only.
 */
function TranscriptionsTab() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [options, setOptionsState] = useState<UploadOptions>(() => loadOptions());
  const [uploading, setUploading] = useState<Uploading[]>([]);
  const list = useQuery({ queryKey: LIST_KEY, queryFn: api.transcriptions });
  const connect = useQuery({ queryKey: ["transcription-connect"], queryFn: api.transcriptionConnect });

  const setOptions = (next: UploadOptions) => {
    setOptionsState(next);
    saveOptions(next);
  };

  /** One after another, in the order given, so they queue in that order too. */
  const addFiles = async (files: File[]) => {
    const entries = files.map((file) => ({ key: nextKey++, name: file.name, fraction: 0 }));
    setUploading((current) => [...current, ...entries]);
    for (const [index, file] of files.entries()) {
      const { key } = entries[index];
      try {
        await upload(file, options, (fraction) =>
          setUploading((current) => current.map((item) => (item.key === key ? { ...item, fraction } : item))),
        ).done;
      } catch (error) {
        toast({
          title: t("transcriptions.uploadFailed").replace("{name}", file.name),
          sub: error instanceof Error ? error.message : String(error),
          tone: "danger",
        });
      } finally {
        setUploading((current) => current.filter((item) => item.key !== key));
        void queryClient.invalidateQueries({ queryKey: LIST_KEY, exact: true });
      }
    }
  };

  const jobs = list.data?.transcriptions ?? [];
  const keepDays = connect.data?.keep_days;

  return (
    <div data-testid="transcriptions-page">
      <p className="mb-6 max-w-prose text-sm text-secondary">{t("transcriptions.intro")}</p>

      <TranscriptionDropzone options={options} onOptions={setOptions} onFiles={(files) => void addFiles(files)} />

      <ul className="space-y-1" data-testid="transcription-list">
        {uploading.map((item) => (
          <li
            key={item.key}
            data-testid="transcription-uploading"
            className="flex items-center gap-4 rounded-lg bg-raised px-4 py-3 shadow-sm"
          >
            <p className="min-w-0 flex-1 truncate text-sm font-medium" dir="auto">
              {item.name}
            </p>
            <p className="text-xs">{t("transcriptions.uploading").replace("{n}", String(Math.round(item.fraction * 100)))}</p>
          </li>
        ))}
        {jobs.map((job) => (
          <TranscriptionRow key={job.id} job={job} />
        ))}
      </ul>

      {list.isLoading && (
        // Rows the shape of a transcription: a name, and its state at the far end.
        <Loading className="space-y-1">
          {[0, 1, 2].map((index) => (
            <div key={index} className="flex items-center gap-4 rounded-lg bg-raised px-4 py-3.5 shadow-sm">
              <Skeleton className={`h-3 ${index === 1 ? "w-1/3" : "w-1/2"}`} />
              <Skeleton className="ms-auto h-3 w-20" />
            </div>
          ))}
        </Loading>
      )}
      {list.isError && !list.data && (
        <EmptyState
          testid="transcriptions-error"
          tone="danger"
          icon={EMPTY_ICON.error}
          title={t("common.error")}
          body={t("transcriptions.errorBody")}
          action={
            <button type="button" onClick={() => void list.refetch()} className={EMPTY_BUTTON}>
              {t("common.retry")}
            </button>
          }
        />
      )}
      {list.data && !jobs.length && !uploading.length && (
        <EmptyState
          testid="transcriptions-empty"
          icon={EMPTY_ICON.transcript}
          title={t("transcriptions.emptyTitle")}
          body={t("transcriptions.empty")}
          action={
            <Link to="/settings#transcription" className={EMPTY_BUTTON}>
              {t("transcriptions.emptyLink")}
            </Link>
          }
        />
      )}
      {keepDays ? (
        <p className="mt-6 text-xs text-tertiary">{t("transcriptions.kept").replace("{n}", String(keepDays))}</p>
      ) : null}
    </div>
  );
}
