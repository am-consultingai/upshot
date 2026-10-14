import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, reason, type AiAppId, type AiApps, type AiAppsFound, type AiAppWhere } from "../api";
import { lineToCopy, terminalToast } from "../lib/aiTerminal";
import { useI18n, type MessageKey } from "../i18n";
import { buttonClass } from "./Button";
import { confirmDialog } from "./ConfirmDialog";
import SettingRow, { SettingGroup } from "./SettingRow";
import { toast } from "./Toaster";
import { ClaudeDesktopRow, Command, CopyButton } from "./TranscriptionServiceSettings";
import { Icon } from "./Icon";
import { ChevronDown, ChevronRight, Globe } from "lucide-react";
import { AppLabel, type AppId } from "../setup/logos";


type CommandKey = keyof AiApps["commands"];
type Side = "windows" | "wsl";

/** An app added with its own command, on Windows or inside WSL (epic z8tj1hjg3v). */
const BY_COMMAND: { id: string; app: AiAppId; label: MessageKey; logo: AppId; windows: CommandKey; wsl: CommandKey }[] = [
  { id: "claude-code", app: "claude_code", label: "settings.transcriptionCode", logo: "claude-code", windows: "claude_code", wsl: "claude_code_wsl" },
  { id: "codex", app: "codex", label: "settings.aiAppsCodex", logo: "codex", windows: "codex", wsl: "codex_wsl" },
  { id: "antigravity", app: "antigravity", label: "settings.aiAppsAntigravity", logo: "antigravity", windows: "antigravity", wsl: "antigravity_wsl" },
];

/** Apps added by a file of their own (Cursor, VS Code, LM Studio…), given as a snippet. */
const BY_FILE: { id: string; label: MessageKey; logos: AppId[]; hint: MessageKey; key: CommandKey }[] = [
  {
    id: "mcp-json",
    label: "settings.aiAppsOthers",
    logos: ["cursor", "lm-studio", "mcp"],
    hint: "settings.aiAppsOthersHint",
    key: "mcp_json",
  },
];

/** Copy ``text``; false when the browser refused (no clipboard, or no permission). */
async function copy(text: string): Promise<boolean> {
  if (!navigator.clipboard) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Opens a terminal with the app's own command typed in; the user presses Enter and the
 * app writes its own file (D98). The line is copied too, for where it cannot be typed in
 * (a source checkout, or PowerShell that blocks it): the toast says to paste it. When no
 * terminal opened, which apps are found is asked again: a WSL distribution may have
 * stopped.
 */
function OpenInTerminal({
  app,
  side,
  text,
  where,
  testid,
}: {
  app: AiAppId;
  side: Side;
  text: string;
  where: AiAppWhere | undefined;
  testid: string;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const open = useMutation({
    mutationFn: async () => {
      const copied = await copy(lineToCopy(side, text, where));
      const data = await api.aiAppsTerminal(app, side);
      return { ...data, copied };
    },
    onSuccess: (data) => {
      const { key, danger } = terminalToast(data.launched, data.copied);
      toast({ title: t(key), ...(danger ? { tone: "danger" as const } : {}) });
      if (!data.launched) void queryClient.invalidateQueries({ queryKey: ["ai-apps-found"] });
    },
    onError: (error) => toast({ title: reason(error), tone: "danger" }),
  });
  return (
    <button
      type="button"
      data-testid={testid}
      disabled={open.isPending}
      onClick={() => open.mutate()}
      className={buttonClass("primary", "md")}
    >
      {t("settings.aiAppsOpenTerminal")}
    </button>
  );
}

function CommandRow({
  row,
  info,
  where,
}: {
  row: (typeof BY_COMMAND)[number];
  info: AiApps | undefined;
  where: AiAppWhere | undefined;
}) {
  const { t } = useI18n();
  // Opens on the side the app was found on, Windows first; the user's click wins.
  const [chosen, setChosen] = useState<Side | null>(null);
  const tab: Side = chosen ?? (where && !where.windows && where.wsl ? "wsl" : "windows");
  const text = info?.commands[tab === "wsl" ? row.wsl : row.windows];
  const here = tab === "windows" ? Boolean(where?.windows) : Boolean(where?.wsl);
  const id = row.id;
  return (
    <SettingRow
      label={<AppLabel apps={[row.logo]}>{t(row.label)}</AppLabel>}
      description={
        <span data-testid={`ai-app-${id}`}>
          {id === "claude-code" && <span className="block">{t("settings.transcriptionCodeHint")}</span>}
          {info?.bridge && (
            <span role="tablist" className="mt-1 flex gap-1">
              {(["windows", "wsl"] as const).map((name) => (
                <button
                  key={name}
                  type="button"
                  role="tab"
                  aria-selected={tab === name}
                  data-testid={`ai-app-${id}-tab-${name}`}
                  onClick={() => setChosen(name)}
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
          {text && tab === "wsl" && <span className="mt-1 block">{t("settings.transcriptionWslNote")}</span>}
          {id === "codex" && tab === "windows" && info?.bridge && (
            <span className="mt-2 block" data-testid="ai-app-codex-steps">
              {t("settings.aiAppsChatGptSteps")}
              <span className="flex items-center gap-2">
                <Command text={info.bridge} testid="ai-app-codex-path" />
                <CopyButton text={info.bridge} testid="ai-app-codex-path-copy" />
              </span>
            </span>
          )}
          {id === "claude-code" && text && (
            <span className="mt-1 block text-tertiary" data-testid="claude-code-old-name">
              {t("settings.transcriptionCodeOld")}
            </span>
          )}
          {id === "claude-code" && where && !where.windows && !where.wsl && (
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
      {text && (
        <span className="flex flex-wrap justify-end gap-2">
          {here && (
            <OpenInTerminal app={row.app} side={tab} text={text} where={where} testid={`ai-app-${id}-terminal`} />
          )}
          <CopyButton text={text} testid={`ai-app-${id}-copy`} />
        </span>
      )}
    </SettingRow>
  );
}

/** Apps not found on this computer, and the rows nothing can detect, out of the way. */
function MoreApps({ open, onToggle, names, children }: { open: boolean; onToggle: () => void; names: string; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <>
      <button
        type="button"
        data-testid="ai-apps-more"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-start text-sm text-secondary hover:bg-a-200"
      >
        <Icon icon={open ? ChevronDown : ChevronRight} mirror={!open} className="size-4 shrink-0" />
        <span>{t("settings.aiAppsMore")}</span>
        {!open && names && <span className="truncate text-tertiary">{names}</span>}
      </button>
      {open && children}
    </>
  );
}

/**
 * The apps, in one list: those found on this computer first, each with Open in terminal;
 * then, under More apps, the ones not found and the rows nothing can detect (an app's own
 * mcp.json, an app that takes a URL). More apps is open when nothing was found. Found is
 * asked again when the window comes back into view, so an app installed meanwhile moves up.
 */
function AppsList({ info }: { info: AiApps | undefined }) {
  const { t } = useI18n();
  const found = useQuery({ queryKey: ["ai-apps-found"], queryFn: api.aiAppsFound, refetchOnWindowFocus: true });
  const connect = useQuery({ queryKey: ["transcription-connect"], queryFn: api.transcriptionConnect });
  const [more, setMore] = useState<boolean | null>(null);
  const apps: AiAppsFound["apps"] | undefined = found.data?.apps;
  const isFound = (app: AiAppId) => Boolean(apps && (apps[app].windows || apps[app].wsl));

  // Until the answers are in, every app shows as found: nothing jumps into More apps.
  const rows: { id: string; name: string; found: boolean; node: ReactNode }[] = [
    {
      id: "claude-desktop",
      name: t("settings.transcriptionDesktop"),
      found: !connect.data || Boolean(connect.data.claude_desktop),
      node: <ClaudeDesktopRow key="claude-desktop" />,
    },
    ...BY_COMMAND.map((row) => ({
      id: row.id,
      name: t(row.label),
      found: !apps || isFound(row.app),
      node: <CommandRow key={row.id} row={row} info={info} where={apps?.[row.app]} />,
    })),
  ];
  const first = rows.filter((row) => row.found);
  const rest = rows.filter((row) => !row.found);
  const open = more ?? first.length === 0;
  return (
    <SettingGroup title={t("settings.aiAppsConnect")}>
      {first.map((row) => row.node)}
      <MoreApps
        open={open}
        onToggle={() => setMore(!open)}
        names={rest.map((row) => row.name).join(", ")}
      >
        {rest.map((row) => row.node)}
        {BY_FILE.map((app) => {
          const text = info?.commands[app.key];
          return (
            <SettingRow
              key={app.id}
              label={<AppLabel apps={app.logos}>{t(app.label)}</AppLabel>}
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
          label={
            <span className="flex items-center gap-2">
              <Globe size={18} aria-hidden="true" className="shrink-0 text-secondary" />
              {t("settings.aiAppsByUrl")}
            </span>
          }
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
      </MoreApps>
    </SettingGroup>
  );
}

/**
 * Settings → AI apps (D98): the user's own AI tools reading their meetings. `part`
 * "access" is the switch and the access key, first in the section; "apps" is how to add
 * Upshot to each app (`AppsList`). Upshot never edits another app's configuration.
 */
export default function AiAppsSettings({ part }: { part: "access" | "apps" }) {
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

  if (part === "access") {
    return (
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
    );
  }

  return <AppsList info={info} />;
}
