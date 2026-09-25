import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ModelStatus } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import { formatBytes } from "../lib/format";
import { downloadFraction, roomForModel } from "../lib/speech";
import BusyButton from "./BusyButton";
import SettingRow, { SELECT_CLASS } from "./SettingRow";

const PRIMARY = "rounded bg-accent px-2.5 py-1 text-sm text-on-accent disabled:opacity-40";
const SECONDARY = "rounded border border-line px-2.5 py-1 text-sm";

const DEVICE_REASONS: Record<ModelStatus["device_reason"], MessageKey> = {
  gpu: "speech.reasonGpu",
  no_cuda: "speech.reasonNoCuda",
  low_vram: "speech.reasonLowVram",
  vram_unknown: "speech.reasonVramUnknown",
  configured: "speech.reasonConfigured",
};

/** Memory as a person reads it: "8 GB", not "8192 MB". */
function megabytes(mb: number | null): string {
  return formatBytes((mb ?? 0) * 1024 * 1024) ?? "0 MB";
}

/**
 * The speech model's status. Polled once a second while it downloads, which is the only
 * time it moves on its own; otherwise it changes when something here changes it.
 */
export function useModelStatus() {
  return useQuery({
    queryKey: ["model"],
    queryFn: api.model,
    refetchInterval: (query) => (query.state.data?.state === "downloading" ? 1000 : false),
  });
}

/**
 * A speech settings write: the device the model runs on.
 *
 * The model status is asked again after it, since it also says where the model will
 * run. A download already running is stopped first and resumed from its files, as
 * before; the model itself is the same whatever is chosen (D60).
 */
export function useSpeechSave() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const model = queryClient.getQueryData<ModelStatus>(["model"]);
      if (model?.state === "downloading") await api.modelCancel();
      return api.putSettings(values);
    },
    onSuccess: async (next) => {
      queryClient.setQueryData(["settings"], next);
      await queryClient.invalidateQueries({ queryKey: ["model"] });
    },
  });
}

/**
 * The model: what it is, what it weighs, whether the drive can hold it, and the download.
 *
 * Before this the download happened inside the first meeting's transcription — minutes
 * of what looked like a hang, with no progress and no way to stop it. The size and the
 * free space are shown before Download is pressed, and a drive that is too full is said
 * in words before a byte is fetched rather than after the backend refuses.
 */
export function SpeechModelRow() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const model = useModelStatus();
  const settle = (next: ModelStatus) => queryClient.setQueryData(["model"], next);
  const download = useMutation({ mutationFn: api.modelDownload, onSuccess: settle });
  const cancel = useMutation({ mutationFn: api.modelCancel, onSuccess: settle });

  const status = model.data;
  const size = formatBytes(status?.expected_bytes) ?? "—";
  const free = formatBytes(status?.free_bytes) ?? "—";
  const fraction = status ? downloadFraction(status) : null;
  const room = status ? roomForModel(status) : true;
  const noRoom = t("speech.noRoom").replace("{size}", size).replace("{free}", free);

  return (
    <SettingRow
      label={t("speech.model")}
      description={
        <>
          <span className="block">{t("speech.modelHint")}</span>
          {status && (
            <span className="mt-1 block" data-testid="model-facts">
              <bdi className="font-mono" data-testid="model-name">
                {status.repo}
              </bdi>
              {" · "}
              <span data-testid="model-size">
                {t(status.state === "ready" ? "speech.modelSizeReady" : "speech.modelSize").replace(
                  "{size}",
                  size,
                )}
              </span>
              {" · "}
              <span data-testid="model-free">{t("speech.freeSpace").replace("{size}", free)}</span>
            </span>
          )}
        </>
      }
      tone={
        status && (
          <>
            {status.state === "downloading" && (
              <div className="mt-2 max-w-prose">
                <div
                  data-testid="model-progress"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
                  aria-label={t("speech.model")}
                  className="h-1.5 overflow-hidden rounded-full bg-surface-3"
                >
                  <div
                    className="h-full rounded-full bg-accent transition-[width]"
                    style={{ width: `${Math.round((fraction ?? 0) * 100)}%` }}
                  />
                </div>
                <p className="mt-1 text-xs text-secondary" data-testid="model-progress-text">
                  {fraction === null
                    ? t("speech.downloadStarting")
                    : t("speech.downloading")
                        .replace("{done}", formatBytes(status.done_bytes) ?? "0 B")
                        .replace("{total}", formatBytes(status.total_bytes) ?? size)}
                </p>
              </div>
            )}
            {status.state === "cancelled" && (
              <p data-testid="model-cancelled" className="mt-1 text-xs text-secondary">
                {t("speech.cancelled")}
              </p>
            )}
            {status.state === "failed" && (
              <p data-testid="model-error" className="mt-1 text-xs text-danger">
                {status.code === "no_space" ? (
                  noRoom
                ) : (
                  <>
                    {t("speech.failed")} <span className="font-mono">{status.error}</span>
                  </>
                )}
              </p>
            )}
            {status.state !== "ready" && status.state !== "failed" && !room && (
              <p data-testid="model-no-room" className="mt-1 text-xs text-warning">
                {noRoom}
              </p>
            )}
          </>
        )
      }
    >
      {!status && <span className="text-xs text-tertiary">{t("speech.checking")}</span>}
      {status?.state === "ready" && (
        <span data-testid="model-ready" className="text-xs text-success">
          {t("speech.ready")}
        </span>
      )}
      {status?.state === "downloading" && (
        <BusyButton
          data-testid="model-cancel"
          busy={cancel.isPending}
          onClick={() => cancel.mutate()}
          className={SECONDARY}
        >
          {t("speech.cancel")}
        </BusyButton>
      )}
      {status && ["missing", "failed", "cancelled"].includes(status.state) && (
        <BusyButton
          data-testid="model-download"
          busy={download.isPending}
          // A drive that cannot take it is refused here, in words, rather than by the
          // backend a moment later. Unknown sizes are left to the backend to judge.
          disabled={!room}
          onClick={() => download.mutate()}
          className={PRIMARY}
        >
          {t(status.state === "missing" ? "speech.download" : "speech.retry")}
        </BusyButton>
      )}
    </SettingRow>
  );
}

/**
 * Where Whisper runs, and why the automatic choice chose it.
 *
 * The why matters more than the what. "CPU" on a machine with a graphics card reads as
 * a bug unless it says the card has too little memory for the model (ClickUp
 * z8tj1had07), and that is the case a person can do something about.
 */
export function ComputeDeviceRow() {
  const { t } = useI18n();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const model = useModelStatus();
  const save = useSpeechSave();
  const configured =
    ((settings.data?.config as { asr?: { device?: string } } | undefined)?.asr?.device ?? "auto");
  const status = model.data;

  return (
    <SettingRow
      label={t("speech.device")}
      htmlFor="asr-device"
      configKey="asr.device"
      description={
        <>
          <span className="block">{t("speech.deviceHint")}</span>
          {status && (
            <span className="mt-1 block" data-testid="device-plan" data-device={status.device}>
              <span className="font-medium text-secondary">
                {t(status.device === "cuda" ? "speech.willUseGpu" : "speech.willUseCpu")}
              </span>{" "}
              <span data-testid="device-reason" data-reason={status.device_reason}>
                {t(DEVICE_REASONS[status.device_reason])
                  .replace("{vram}", megabytes(status.vram_mb))
                  .replace("{min}", megabytes(status.min_vram_mb))}
              </span>
            </span>
          )}
        </>
      }
    >
      <select
        id="asr-device"
        data-testid="asr-device"
        value={configured}
        onChange={(event) => save.mutate({ "asr.device": event.target.value })}
        className={SELECT_CLASS}
      >
        <option value="auto">{t("speech.deviceAuto")}</option>
        <option value="cpu">{t("speech.deviceCpu")}</option>
        <option value="cuda">{t("speech.deviceGpu")}</option>
      </select>
    </SettingRow>
  );
}
