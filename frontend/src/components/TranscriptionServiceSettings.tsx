import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import SettingRow, { SettingGroup } from "./SettingRow";
import { toast } from "./Toaster";

function CopyButton({ text, testid }: { text: string; testid: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setCopied(true));
      }}
      className="h-8 shrink-0 rounded-md bg-surface-2 px-3 text-sm hover:bg-a-200"
    >
      {copied ? t("settings.transcriptionCopied") : t("settings.transcriptionCopy")}
    </button>
  );
}

function Command({ text, testid }: { text: string; testid: string }) {
  return (
    <code
      data-testid={`${testid}-text`}
      dir="ltr"
      className="block max-w-full overflow-x-auto whitespace-pre rounded-md bg-surface-2 px-2.5 py-1.5 font-mono text-2xs"
    >
      {text}
    </code>
  );
}

function CommandRow({
  label,
  hint,
  text,
  testid,
  note,
}: {
  label: MessageKey;
  hint: MessageKey;
  text: string | null;
  testid: string;
  note?: MessageKey;
}) {
  const { t } = useI18n();
  return (
    <SettingRow
      label={t(label)}
      description={
        <>
          {t(hint)}
          {text && <Command text={text} testid={testid} />}
          {text && note && <span className="mt-1 block">{t(note)}</span>}
          {!text && <span className="mt-1 block">{t("settings.transcriptionInstalledOnly")}</span>}
        </>
      }
    >
      {text && <CopyButton text={text} testid={testid} />}
    </SettingRow>
  );
}

/**
 * Settings → "Transcription for other apps" (D86, R3): the switch, and how to connect
 * Claude in one step, with this install's real path and port.
 */
export default function TranscriptionServiceSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const connect = useQuery({ queryKey: ["transcription-connect"], queryFn: api.transcriptionConnect });
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const save = useMutation({
    mutationFn: (next: boolean) => api.putSettings({ "transcription.service_enabled": next }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["transcription-connect"] });
    },
  });
  const addToDesktop = useMutation({
    mutationFn: api.addToClaudeDesktop,
    onSuccess: () => toast({ title: t("settings.transcriptionOpened") }),
    onError: (error) => toast({ title: reason(error), tone: "danger" }),
  });
  const info = connect.data;
  const on = enabled ?? info?.enabled ?? true;

  return (
    <>
      <SettingGroup>
        <SettingRow
          label={t("settings.transcriptionSwitch")}
          htmlFor="transcription-enabled"
          configKey="transcription.service_enabled"
          description={<span data-testid="transcription-switch-hint">{t("settings.transcriptionSwitchHint")}</span>}
        >
          <input
            id="transcription-enabled"
            data-testid="transcription-enabled"
            type="checkbox"
            role="switch"
            checked={on}
            onChange={(event) => {
              setEnabled(event.target.checked);
              save.mutate(event.target.checked);
            }}
            className="size-4 accent-[var(--accent)]"
          />
        </SettingRow>
      </SettingGroup>

      <SettingGroup title={t("settings.transcriptionConnect")}>
        <SettingRow
          label={t("settings.transcriptionDesktop")}
          description={
            info?.bundle_available ? t("settings.transcriptionDesktopHint") : t("settings.transcriptionInstalledOnly")
          }
        >
          <button
            type="button"
            data-testid="transcription-add-desktop"
            disabled={!info?.bundle_available || addToDesktop.isPending}
            onClick={() => addToDesktop.mutate()}
            className="h-8 rounded-md bg-accent px-3 text-sm font-medium text-on-accent hover:bg-accent-hover disabled:opacity-50"
          >
            {t("settings.transcriptionAddDesktop")}
          </button>
        </SettingRow>
        <CommandRow
          label="settings.transcriptionWsl"
          hint="settings.transcriptionWslHint"
          note="settings.transcriptionWslNote"
          text={info?.wsl_command ?? null}
          testid="transcription-copy-wsl"
        />
        <CommandRow
          label="settings.transcriptionWindows"
          hint="settings.transcriptionWindowsHint"
          text={info?.windows_command ?? null}
          testid="transcription-copy-windows"
        />
        <SettingRow
          label={t("settings.transcriptionConfig")}
          description={
            <>
              {t("settings.transcriptionConfigHint")}
              {info?.desktop_config && <Command text={info.desktop_config} testid="transcription-copy-config" />}
              {info?.desktop_config_paths?.length ? (
                <span className="mt-1 block" dir="ltr">
                  {info.desktop_config_paths.join(" · ")}
                </span>
              ) : null}
            </>
          }
        >
          {info?.desktop_config && <CopyButton text={info.desktop_config} testid="transcription-copy-config" />}
        </SettingRow>
      </SettingGroup>

      <SettingGroup title={t("settings.transcriptionDevelopers")}>
        <SettingRow
          label={t("settings.transcriptionApiUrl")}
          description={
            <>
              {info && <Command text={info.api_url} testid="transcription-api-url" />}
              {info && <Command text={info.curl_example} testid="transcription-curl" />}
              <span className="mt-1 block">{t("settings.transcriptionMirrored")}</span>
            </>
          }
        >
          {info && <CopyButton text={info.curl_example} testid="transcription-copy-curl" />}
        </SettingRow>
      </SettingGroup>
    </>
  );
}
