import type { AiAppWhere } from "../api";
import type { MessageKey } from "../i18n";

/**
 * Settings → MCP, Open in terminal (epic z8tj1hjg3v): what to copy at the click, and
 * what the toast says afterwards.
 */

/** The line to copy: on Windows, the one the terminal will type (`codex.cmd …`, or the
 * program's full path, where that is what runs here); otherwise the command shown. It is
 * copied at the click, while the browser still allows it: after the request the new
 * terminal may already have the focus, and the clipboard would refuse. */
export function lineToCopy(side: "windows" | "wsl", shown: string, where: AiAppWhere | undefined): string {
  return side === "windows" && where?.windows_line ? where.windows_line : shown;
}

/** Opened: press Enter there. Not opened but copied: paste it. Neither: use Copy. */
export function terminalToast(launched: boolean, copied: boolean): { key: MessageKey; danger: boolean } {
  if (launched) return { key: "settings.aiAppsTerminalOpened", danger: false };
  if (copied) return { key: "settings.aiAppsTerminalCopied", danger: false };
  return { key: "settings.aiAppsTerminalNotCopied", danger: true };
}

/** The sides a row offers: where the app was found; both when it was found nowhere, or
 * before the answer is in (nothing to go on). Windows first. */
export function sidesToShow(where: AiAppWhere | undefined): ("windows" | "wsl")[] {
  const found: ("windows" | "wsl")[] = [];
  if (where?.windows) found.push("windows");
  if (where?.wsl) found.push("wsl");
  return found.length ? found : ["windows", "wsl"];
}
