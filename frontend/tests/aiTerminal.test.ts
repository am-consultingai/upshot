import { describe, expect, it } from "vitest";
import { lineToCopy, terminalToast } from "../src/lib/aiTerminal";

const SHOWN = 'codex mcp add upshot -- "C:\\Users\\someone\\upshot-mcp.exe"';
const TYPED = 'codex.cmd mcp add upshot -- "C:\\Users\\someone\\upshot-mcp.exe"';

describe("Open in terminal", () => {
  it("copies the line the Windows terminal types, not the one shown", () => {
    expect(lineToCopy("windows", SHOWN, { windows: true, wsl: null, windows_line: TYPED })).toBe(TYPED);
  });

  it("copies the shown command in WSL, or before the answers are in", () => {
    expect(lineToCopy("wsl", SHOWN, { windows: true, wsl: "Ubuntu", windows_line: TYPED })).toBe(SHOWN);
    expect(lineToCopy("windows", SHOWN, undefined)).toBe(SHOWN);
    expect(lineToCopy("windows", SHOWN, { windows: false, wsl: null, windows_line: null })).toBe(SHOWN);
  });

  it("says what to do next, and warns when nothing was opened or copied", () => {
    expect(terminalToast(true, false)).toEqual({ key: "settings.aiAppsTerminalOpened", danger: false });
    expect(terminalToast(false, true)).toEqual({ key: "settings.aiAppsTerminalCopied", danger: false });
    expect(terminalToast(false, false)).toEqual({ key: "settings.aiAppsTerminalNotCopied", danger: true });
  });
});

describe("the sides a row offers", () => {
  it("only those where the app was found, Windows first", async () => {
    const { sidesToShow } = await import("../src/lib/aiTerminal");
    expect(sidesToShow({ windows: true, wsl: null, windows_line: null })).toEqual(["windows"]);
    expect(sidesToShow({ windows: false, wsl: "Ubuntu", windows_line: null })).toEqual(["wsl"]);
    expect(sidesToShow({ windows: true, wsl: "Ubuntu", windows_line: null })).toEqual(["windows", "wsl"]);
  });

  it("both when found nowhere, or before the answer is in", async () => {
    const { sidesToShow } = await import("../src/lib/aiTerminal");
    expect(sidesToShow({ windows: false, wsl: null, windows_line: null })).toEqual(["windows", "wsl"]);
    expect(sidesToShow(undefined)).toEqual(["windows", "wsl"]);
  });
});
