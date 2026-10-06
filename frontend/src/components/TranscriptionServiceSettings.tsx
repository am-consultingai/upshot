import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason } from "../api";
import { useI18n } from "../i18n";
import SettingRow, { SettingGroup } from "./SettingRow";
import { toast } from "./Toaster";

const CLAUDE_DOWNLOAD = "https://claude.ai/download";

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
      className="mt-1.5 block max-w-full overflow-x-auto whitespace-pre rounded-md bg-surface-2 px-2.5 py-1.5 font-mono text-2xs"
    >
      {text}
    </code>
  );
}

/**
 * Settings → "Transcription for other apps" (D86, R3; plan revision 5): the switch, then
 * one card for each Claude. Upshot never edits Claude's files: Claude Desktop is handed the
 * extension and shows its own install window, and Claude Code is given a command to paste.
 * Whether Upshot is already added there is not shown: knowing would mean reading Claude's
 * own files.
 */
export default function TranscriptionServiceSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const connect = useQuery({ queryKey: ["transcription-connect"], queryFn: api.transcriptionConnect });
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [tab, setTab] = useState<"windows" | "wsl">("windows");
  const [byHand, setByHand] = useState(false);
  const save = useMutation({
    mutationFn: (next: boolean) => api.putSettings({ "transcription.service_enabled": next }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["transcription-connect"] });
    },
  });
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
  const on = enabled ?? info?.enabled ?? true;
  const command = tab === "windows" ? info?.windows_command : info?.wsl_command;
  const installedApp = Boolean(info?.bridge);

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
            <span data-testid="claude-desktop-card">
              {t("settings.transcriptionDesktopFor")}
              {!installedApp ? (
                <span className="mt-1 block">{t("settings.transcriptionInstalledOnly")}</span>
              ) : info?.claude_desktop ? (
                <>
                  <span className="mt-1 block">{t("settings.transcriptionDesktopHint")}</span>
                  <span className="mt-1 block text-tertiary" data-testid="claude-desktop-warning">
                    {t("settings.transcriptionWarning")}
                  </span>
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
              className="h-8 rounded-md bg-accent px-3 text-sm font-medium text-on-accent hover:bg-accent-hover disabled:opacity-50"
            >
              {t("settings.transcriptionAddDesktop")}
            </button>
          )}
        </SettingRow>

        <SettingRow
          label={t("settings.transcriptionCode")}
          description={
            <span data-testid="claude-code-card">
              {t("settings.transcriptionCodeHint")}
              {installedApp ? (
                <>
                  <span role="tablist" className="mt-2 flex gap-1">
                    {(["windows", "wsl"] as const).map((name) => (
                      <button
                        key={name}
                        type="button"
                        role="tab"
                        aria-selected={tab === name}
                        data-testid={`claude-code-tab-${name}`}
                        onClick={() => setTab(name)}
                        className={`h-6 rounded-md px-2 text-xs ${tab === name ? "bg-a-200 text-primary" : "text-secondary hover:bg-a-200"}`}
                      >
                        {t(name === "windows" ? "settings.transcriptionTabWindows" : "settings.transcriptionTabWsl")}
                      </button>
                    ))}
                  </span>
                  {command && <Command text={command} testid={`transcription-copy-${tab}`} />}
                  {tab === "wsl" && <span className="mt-1 block">{t("settings.transcriptionWslNote")}</span>}
                  <span className="mt-1 block">{t("settings.transcriptionCodeTry")}</span>
                </>
              ) : (
                <span className="mt-1 block">{t("settings.transcriptionInstalledOnly")}</span>
              )}
              {info && !info.claude_code && (
                <span className="mt-1 block" data-testid="claude-code-missing">
                  {t("settings.transcriptionCodeMissing")}{" "}
                  <Link to="/settings#summaries" className="underline">
                    {t("settings.transcriptionCodeInstall")}
                  </Link>
                </span>
              )}
            </span>
          }
        >
          {installedApp && command && <CopyButton text={command} testid={`transcription-copy-${tab}`} />}
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
