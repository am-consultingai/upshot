import { useState } from "react";
import { buttonClass } from "./Button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason } from "../api";
import { useI18n } from "../i18n";
import SettingRow, { SettingGroup } from "./SettingRow";
import { toast } from "./Toaster";
import { AppLabel } from "../setup/logos";

const CLAUDE_DOWNLOAD = "https://claude.ai/download";

export function CopyButton({ text, testid }: { text: string; testid: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setCopied(true));
      }}
      className={buttonClass("secondary", "md")}
    >
      {copied ? t("settings.transcriptionCopied") : t("settings.transcriptionCopy")}
    </button>
  );
}

export function Command({ text, testid }: { text: string; testid: string }) {
  return (
    <code
      data-testid={`${testid}-text`}
      dir="ltr"
      className="mt-1.5 block max-w-full overflow-x-auto whitespace-pre rounded-md bg-surface-2 px-2.5 py-1.5 font-mono text-2xs"
    >
      {text}
    </code>
  );
}

/**
 * Claude Desktop's row in Settings → MCP (D86, R3): Upshot hands Claude the extension
 * and Claude shows its own install window. Upshot never edits Claude's files, and whether
 * Upshot is already added there is not shown: knowing would mean reading them.
 */
export function ClaudeDesktopRow() {
  const { t } = useI18n();
  const connect = useQuery({ queryKey: ["transcription-connect"], queryFn: api.transcriptionConnect });
  const [byHand, setByHand] = useState(false);
  const failed = (error: unknown) => toast({ title: reason(error), tone: "danger" });
  const addToDesktop = useMutation({
    mutationFn: api.addToClaudeDesktop,
    onSuccess: () => toast({ title: t("settings.transcriptionOpened") }),
    onError: (error) => {
      setByHand(true);
      failed(error);
    },
  });
  const showFile = useMutation({
    mutationFn: api.showClaudeExtension,
    onSuccess: () => {
      setByHand(true);
      toast({ title: t("settings.transcriptionShownFile") });
    },
    onError: failed,
  });
  const info = connect.data;
  const installedApp = Boolean(info?.bridge);
  return (
    <SettingRow
      label={<AppLabel apps={["claude"]}>{t("settings.transcriptionDesktop")}</AppLabel>}
      description={
        <span data-testid="claude-desktop-card">
          {!installedApp ? (
            <span className="block">{t("settings.transcriptionInstalledOnly")}</span>
          ) : info?.claude_desktop ? (
            <>
              <span className="block">{t("settings.transcriptionDesktopHint")}</span>
              <span className="mt-1.5 flex flex-wrap items-center gap-x-1.5">
                <span>{t("settings.transcriptionDidntOpen")}</span>
                <button
                  type="button"
                  data-testid="claude-show-file"
                  onClick={() => showFile.mutate()}
                  className="underline"
                >
                  {t("settings.transcriptionShowFile")}
                </button>
                <span aria-hidden="true">·</span>
                <button
                  type="button"
                  data-testid="claude-by-hand"
                  aria-expanded={byHand}
                  onClick={() => setByHand(!byHand)}
                  className="underline"
                >
                  {t("settings.transcriptionByHand")}
                </button>
              </span>
              {byHand && (
                <span className="mt-1 block" data-testid="claude-by-hand-steps">
                  {t("settings.transcriptionByHandSteps")}
                </span>
              )}
            </>
          ) : (
            <span className="mt-1 block" data-testid="claude-desktop-missing">
              {t("settings.transcriptionDesktopMissing")}{" "}
              <a href={CLAUDE_DOWNLOAD} target="_blank" rel="noreferrer" className="underline">
                {t("settings.transcriptionDesktopDownload")}
              </a>
            </span>
          )}
        </span>
      }
    >
      {installedApp && info?.claude_desktop && (
        <button
          type="button"
          data-testid="transcription-add-desktop"
          disabled={addToDesktop.isPending}
          onClick={() => addToDesktop.mutate()}
          className={buttonClass("primary", "md")}
        >
          {t("settings.transcriptionAddDesktop")}
        </button>
      )}
    </SettingRow>
  );
}

/**
 * Settings → MCP, further down: the switch for transcription by other apps (D86), and
 * the API for developers. Connecting each app is in `AiAppsSettings`.
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
