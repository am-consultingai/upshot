import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import MicMeter from "./MicMeter";
import SettingRow, { SELECT_CLASS } from "./SettingRow";

/**
 * The microphone and the computer's audio, each with its live meter.
 *
 * Settings and first-run setup both show these, and they are the same two rows on
 * purpose: a device chosen while setting up is the device Settings shows, and a fix
 * to how a meter opens its stream fixes both. They read and write the shared
 * `settings` query, so choosing in one place is reflected in the other.
 */
export default function AudioDeviceSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const audio = useQuery({ queryKey: ["audio-devices"], queryFn: api.audioDevices });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const config = (settings.data?.config ?? {}) as {
    audio?: { input_device?: number | null; output_device?: number | null };
  };
  const devices = audio.data?.devices ?? [];
  const outputs = audio.data?.outputs ?? [];
  const selectedDevice = config.audio?.input_device ?? null;
  const selectedOutput = config.audio?.output_device ?? null;

  return (
    <>
      <SettingRow
        label={t("settings.microphone")}
        htmlFor="mic-device"
        configKey="audio.input_device"
        description={
          devices.length === 0 ? (
            <>
              {/* Still asking is not "none found": the list takes a moment on Windows,
                  and a stranger reads "No microphone" as "my microphone is broken". */}
              {!audio.data && !audio.isError
                ? t("settings.micLooking")
                : audio.data && audio.data.platform !== "win32"
                  ? t("settings.micWrongHost")
                  : t("settings.micUnavailable")}
              {audio.data?.error && (
                <span data-testid="mic-error" className="mt-1 block font-mono">
                  {audio.data.error}
                </span>
              )}
              {audio.data && (
                <span className="mt-1 block">
                  host: {audio.data.platform} · capture: {audio.data.capture}
                </span>
              )}
            </>
          ) : undefined
        }
      >
        {devices.length === 0 ? (
          <span data-testid="mic-unavailable" className="text-xs text-tertiary">
            —
          </span>
        ) : (
          <select
            id="mic-device"
            data-testid="mic-device"
            value={selectedDevice === null ? "" : String(selectedDevice)}
            onChange={(event) => {
              const raw = event.target.value;
              save.mutate({ "audio.input_device": raw === "" ? null : Number(raw) });
            }}
            className={SELECT_CLASS}
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
        {/*
         * Not mounted until the saved device is known. The meter opens the device
         * in an effect keyed on that prop, so rendering it while the setting is
         * still loading opens the default device and then immediately reopens the
         * real one — two opens per meter, for nothing.
         */}
        {settings.isSuccess && (
          <div className="w-40">
            <MicMeter device={selectedDevice} track="me" />
          </div>
        )}
      </SettingRow>

      <SettingRow
        label={t("settings.systemAudio")}
        htmlFor="output-device"
        description={t("settings.systemAudioNote")}
      >
        <select
          id="output-device"
          data-testid="output-device"
          value={selectedOutput === null ? "" : String(selectedOutput)}
          onChange={(event) => {
            const raw = event.target.value;
            save.mutate({ "audio.output_device": raw === "" ? null : Number(raw) });
          }}
          className={SELECT_CLASS}
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
        {settings.isSuccess && (
          <div className="w-40">
            <MicMeter device={null} track="them" hint={t("settings.systemAudioLevel")} />
          </div>
        )}
      </SettingRow>
    </>
  );
}
