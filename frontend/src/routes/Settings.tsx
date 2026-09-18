import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type Locale } from "../i18n";
import ProviderSettings from "../components/ProviderSettings";
import PromptSettings from "../components/PromptSettings";
import MicMeter from "../components/MicMeter";
import BusyButton from "../components/BusyButton";

/** Language endonyms are data, not copy: they are never translated. */
const LANGUAGE_NAMES: Record<string, string> = { en: "English", he: "עברית", auto: "auto" };

interface ConfigShape {
  data_root?: string | null;
  ui?: { language?: string };
  summary?: { language?: string };
  audio?: { input_device?: number | null; output_device?: number | null };
  detection?: { mode?: string };
}

/** What the three detection modes mean, said in terms of what happens to you. */
const DETECTION_MODES = [
  { value: "shadow", label: "settings.detectionWatch" },
  { value: "on", label: "settings.detectionAuto" },
  { value: "off", label: "settings.detectionOff" },
] as const;

export default function Settings() {
  const { t, locale, setLocale } = useI18n();
  const queryClient = useQueryClient();
  const [dataRoot, setDataRoot] = useState("");
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const audio = useQuery({ queryKey: ["audio-devices"], queryFn: api.audioDevices });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const config = (settings.data?.config ?? {}) as ConfigShape;

  useEffect(() => {
    if (config.data_root !== undefined && config.data_root !== null) setDataRoot(config.data_root);
  }, [config.data_root]);

  const warnings = settings.data?.warnings ?? [];
  const localWarning = /onedrive|dropbox|google drive/i.test(dataRoot);

  const devices = audio.data?.devices ?? [];
  const outputs = audio.data?.outputs ?? [];
  const selectedDevice = config.audio?.input_device ?? null;
  const selectedOutput = config.audio?.output_device ?? null;

  return (
    <section data-testid="settings-page">
      {/* Selector and meter share a row: neither needs the full width, and side by side
          the level reads as belonging to the device above it. */}
      <div className="mb-6 grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="mic-device">
            {t("settings.microphone")}
          </label>
          {devices.length === 0 ? (
            <div data-testid="mic-unavailable" className="text-sm text-secondary">
              <p>
                {audio.data && audio.data.platform !== "win32"
                  ? t("settings.micWrongHost")
                  : t("settings.micUnavailable")}
              </p>
              {audio.data?.error && (
                <p data-testid="mic-error" className="mt-1 font-mono text-xs text-tertiary">
                  {audio.data.error}
                </p>
              )}
              {audio.data && (
                <p className="mt-1 text-xs text-tertiary">
                  host: {audio.data.platform} · capture: {audio.data.capture}
                </p>
              )}
            </div>
          ) : (
            <select
              id="mic-device"
              data-testid="mic-device"
              value={selectedDevice === null ? "" : String(selectedDevice)}
              onChange={(event) => {
                const raw = event.target.value;
                save.mutate({ "audio.input_device": raw === "" ? null : Number(raw) });
              }}
              className="w-full rounded border border-line px-2 py-1"
            >
              <option value="">{t("settings.microphoneDefault")}</option>
              {devices.map((device) => (
                <option key={device.index} value={device.index}>
                  {device.name}
                  {device.is_default ? " ✓" : ""}
                </option>
              ))}
            </select>
          )}
        </div>
        <MicMeter device={selectedDevice} track="me" />

        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="output-device">
            {t("settings.systemAudio")}
          </label>
          <select
            id="output-device"
            data-testid="output-device"
            value={selectedOutput === null ? "" : String(selectedOutput)}
            onChange={(event) => {
              const raw = event.target.value;
              save.mutate({ "audio.output_device": raw === "" ? null : Number(raw) });
            }}
            className="w-full rounded border border-line px-2 py-1"
            disabled={outputs.length === 0}
          >
            <option value="">{t("settings.outputDefault")}</option>
            {outputs.map((device) => (
              <option key={device.index} value={device.index}>
                {device.name}
                {device.is_default ? " ✓" : ""}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-tertiary">{t("settings.systemAudioNote")}</p>
        </div>
        <MicMeter device={null} track="them" hint={t("settings.systemAudioLevel")} />
      </div>

      <label className="mb-1 block text-sm font-medium" htmlFor="data-root">
        {t("settings.dataRoot")}
      </label>
      <input
        id="data-root"
        data-testid="data-root"
        value={dataRoot}
        onChange={(event) => setDataRoot(event.target.value)}
        className="w-full rounded border border-line px-2 py-1"
      />
      {(localWarning || warnings.length > 0) && (
        <p data-testid="sync-warning" className="mt-1 text-sm text-warning">
          {t("settings.syncWarning")}
        </p>
      )}

      <label className="mt-4 mb-1 block text-sm font-medium" htmlFor="ui-language">
        {t("settings.language")}
      </label>
      <select
        id="ui-language"
        data-testid="ui-language"
        value={locale}
        onChange={(event) => {
          const next = event.target.value as Locale;
          setLocale(next);
          save.mutate({ "ui.language": next });
        }}
        className="rounded border border-line px-2 py-1"
      >
        {["en", "he"].map((code) => (
          <option key={code} value={code}>
            {LANGUAGE_NAMES[code]}
          </option>
        ))}
      </select>

      <label className="mt-4 mb-1 block text-sm font-medium" htmlFor="summary-language">
        {t("settings.summaryLanguage")}
      </label>
      <select
        id="summary-language"
        data-testid="summary-language"
        value={config.summary?.language ?? "en"}
        onChange={(event) => save.mutate({ "summary.language": event.target.value })}
        className="rounded border border-line px-2 py-1"
      >
        {["en", "he", "auto"].map((code) => (
          <option key={code} value={code}>
            {LANGUAGE_NAMES[code]}
          </option>
        ))}
      </select>

      <label className="mt-4 mb-1 block text-sm font-medium" htmlFor="detection-mode">
        {t("settings.detection")}
      </label>
      <select
        id="detection-mode"
        data-testid="detection-mode"
        value={config.detection?.mode ?? "shadow"}
        onChange={(event) => save.mutate({ "detection.mode": event.target.value })}
        className="rounded border border-line px-2 py-1"
      >
        {DETECTION_MODES.map((option) => (
          <option key={option.value} value={option.value}>
            {t(option.label)}
          </option>
        ))}
      </select>
      <p className="mt-1 max-w-3xl text-xs text-secondary" data-testid="detection-hint">
        {t("settings.detectionHint")}
      </p>

      <ProviderSettings />
      <PromptSettings />

      <div className="mt-4">
        <BusyButton
          data-testid="settings-save"
          busy={save.isPending}
          onClick={() => save.mutate({ data_root: dataRoot })}
          className="rounded bg-accent px-3 py-1.5 text-on-accent"
        >
          {t("settings.save")}
        </BusyButton>
        {save.isSuccess && (
          <span data-testid="settings-saved" className="ms-2 text-sm text-success">
            {t("settings.saved")}
          </span>
        )}
      </div>
    </section>
  );
}
