import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason, type AiApps } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import { buttonClass } from "./Button";
import { confirmDialog } from "./ConfirmDialog";
import SettingRow, { SettingGroup } from "./SettingRow";
import { toast } from "./Toaster";
import { Command, CopyButton } from "./TranscriptionServiceSettings";

type CommandKey = keyof AiApps["commands"];

/** An app added with its own command: on Windows, or inside WSL. */
const BY_COMMAND: { id: string; label: MessageKey; windows: CommandKey; wsl?: CommandKey }[] = [
  { id: "codex", label: "settings.aiAppsCodex", windows: "codex", wsl: "codex_wsl" },
  { id: "antigravity", label: "settings.aiAppsAntigravity", windows: "antigravity", wsl: "antigravity_wsl" },
  { id: "gemini", label: "settings.aiAppsGemini", windows: "gemini" },
];

/** An app added by a file of its own, given as a snippet. */
const BY_FILE: { id: string; label: MessageKey; hint: MessageKey; key: CommandKey }[] = [
  { id: "mcp-json", label: "settings.aiAppsOthers", hint: "settings.aiAppsOthersHint", key: "mcp_json" },
  { id: "vscode", label: "settings.aiAppsVsCode", hint: "settings.aiAppsVsCodeHint", key: "vscode_json" },
];

function CommandRow({
  id,
  label,
  windows,
  wsl,
  info,
}: {
  id: string;
  label: MessageKey;
  windows: CommandKey;
  wsl?: CommandKey;
  info: AiApps | undefined;
}) {
  const { t } = useI18n();
  const [tab, setTab] = useState<"windows" | "wsl">("windows");
  const text = info?.commands[tab === "wsl" && wsl ? wsl : windows];
  return (
    <SettingRow
      label={t(label)}
      description={
        <span data-testid={`ai-app-${id}`}>
          {wsl && info?.bridge && (
            <span role="tablist" className="mt-1 flex gap-1">
              {(["windows", "wsl"] as const).map((name) => (
                <button
                  key={name}
                  type="button"
                  role="tab"
                  aria-selected={tab === name}
                  data-testid={`ai-app-${id}-tab-${name}`}
                  onClick={() => setTab(name)}
                  className={`h-6 rounded-md px-2 text-xs ${tab === name ? "bg-a-200 text-primary" : "text-secondary hover:bg-a-200"}`}
                >
                  {t(name === "windows" ? "settings.transcriptionTabWindows" : "settings.transcriptionTabWsl")}
                </button>
              ))}
            </span>
          )}
          {text ? (
            <Command text={text} testid={`ai-app-${id}`} />
          ) : (
            <span className="mt-1 block">{t("settings.transcriptionInstalledOnly")}</span>
          )}
        </span>
      }
    >
      {text && <CopyButton text={text} testid={`ai-app-${id}-copy`} />}
    </SettingRow>
  );
}

/**
 * Settings → AI apps (D98): the user's own AI tools reading their meetings. The switch,
 * the access key, and how to add Upshot to each app: its own command, or a snippet for
 * its own file. Upshot never edits another app's configuration.
 */
export default function AiAppsSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: ["ai-apps"], queryFn: api.aiApps });
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const failed = (error: unknown) => toast({ title: reason(error), tone: "danger" });
  const save = useMutation({
    mutationFn: (next: boolean) => api.putSettings({ "mcp.meetings_enabled": next }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["ai-apps"] });
    },
    onError: (error) => {
      // Show what the server still does, not the value that failed to save.
      setEnabled(null);
      failed(error);
    },
  });
  const reveal = useMutation({ mutationFn: api.aiAppsKey, onSuccess: (data) => setKey(data.key), onError: failed });
  const reset = useMutation({
    mutationFn: api.aiAppsResetKey,
    onSuccess: (data) => {
      setKey(data.key);
      toast({ title: t("settings.aiAppsKeyNew") });
    },
    onError: failed,
  });
  const info = overview.data;
  const on = enabled ?? info?.meetings_enabled ?? true;

  return (
    <>
      <SettingGroup>
        <SettingRow
          label={t("settings.aiAppsSwitch")}
          htmlFor="ai-apps-enabled"
          configKey="mcp.meetings_enabled"
          description={<span data-testid="ai-apps-switch-hint">{t("settings.aiAppsSwitchHint")}</span>}
        >
          <input
            id="ai-apps-enabled"
            data-testid="ai-apps-enabled"
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
        <SettingRow
          label={t("settings.aiAppsKey")}
          description={
            <span data-testid="ai-apps-key-hint">
              {t("settings.aiAppsKeyHint")}
              {key && <Command text={key} testid="ai-apps-key" />}
            </span>
          }
        >
          <span className="flex flex-wrap gap-2">
            {key ? (
              <>
                <CopyButton text={key} testid="ai-apps-key-copy" />
                <button
                  type="button"
                  data-testid="ai-apps-key-hide"
                  onClick={() => setKey(null)}
                  className={buttonClass("ghost", "md")}
                >
                  {t("settings.aiAppsKeyHide")}
                </button>
              </>
            ) : (
              <button
                type="button"
                data-testid="ai-apps-key-show"
                disabled={reveal.isPending}
                onClick={() => reveal.mutate()}
                className={buttonClass("secondary", "md")}
              >
                {t("settings.aiAppsKeyShow")}
              </button>
            )}
            <button
              type="button"
              data-testid="ai-apps-key-reset"
              disabled={reset.isPending}
              onClick={() =>
                void confirmDialog({
                  title: t("settings.aiAppsKeyResetTitle"),
                  body: t("settings.aiAppsKeyResetBody"),
                  confirm: t("settings.aiAppsKeyReset"),
                  cancel: t("common.cancel"),
                }).then((yes) => yes && reset.mutate())
              }
              className={buttonClass("ghost", "md")}
            >
              {t("settings.aiAppsKeyReset")}
            </button>
          </span>
        </SettingRow>
      </SettingGroup>

      <SettingGroup title={t("settings.aiAppsConnect")}>
        {BY_COMMAND.map((app) => (
          <CommandRow key={app.id} id={app.id} label={app.label} windows={app.windows} wsl={app.wsl} info={info} />
        ))}
        {BY_FILE.map((app) => {
          const text = info?.commands[app.key];
          return (
            <SettingRow
              key={app.id}
              label={t(app.label)}
              description={
                <span data-testid={`ai-app-${app.id}`}>
                  {t(app.hint)}
                  {text ? (
                    <Command text={text} testid={`ai-app-${app.id}`} />
                  ) : (
                    <span className="mt-1 block">{t("settings.transcriptionInstalledOnly")}</span>
                  )}
                </span>
              }
            >
              {text && <CopyButton text={text} testid={`ai-app-${app.id}-copy`} />}
            </SettingRow>
          );
        })}
        <SettingRow
          label={t("settings.aiAppsByUrl")}
          description={
            <span data-testid="ai-app-url">
              {t("settings.aiAppsByUrlHint")}
              {info && (
                <>
                  <span className="mt-2 block text-xs">{t("settings.aiAppsUrlLocal")}</span>
                  <Command text={info.urls.local} testid="ai-apps-url-local" />
                  <span className="mt-2 block text-xs">{t("settings.aiAppsUrlDocker")}</span>
                  <Command text={info.urls.docker} testid="ai-apps-url-docker" />
                  {info.port_moved && (
                    <span className="mt-2 block text-tertiary" data-testid="ai-apps-port-moved">
                      {t("settings.aiAppsPortMoved")
                        .replace("{port}", String(info.port))
                        .replace("{usual}", String(info.usual_port))}
                    </span>
                  )}
                </>
              )}
            </span>
          }
        >
          {info && <CopyButton text={info.urls.local} testid="ai-apps-url-copy" />}
        </SettingRow>
      </SettingGroup>
    </>
  );
}
