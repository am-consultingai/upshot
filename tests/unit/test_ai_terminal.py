"""Settings → AI apps: finding the user's AI apps, and the terminal with each one's own
command typed in (ClickUp epic z8tj1hjg3v). Upshot edits no other app's files (D98)."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app import ai_terminal

BRIDGE = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe"
CODEX = f'codex mcp add upshot -- "{BRIDGE}"'


def test_running_distros_are_read_from_utf16_and_docker_is_left_out() -> None:
    raw = "Ubuntu-24.04\r\ndocker-desktop\r\ndocker-desktop-data\r\nDebian\r\n".encode("utf-16-le")
    assert ai_terminal.parse_distros(raw) == ["Ubuntu-24.04", "Debian"]
    assert ai_terminal.parse_distros(b"") == []


def test_only_paths_that_command_v_printed_count_as_found() -> None:
    out = "/home/someone/.local/bin/claude\nalias codex='npx codex'\n/usr/local/bin/agy\n"
    assert ai_terminal.apps_in(out) == ["claude_code", "antigravity"]
    assert ai_terminal.apps_in("bash: no job control in this shell\n") == []


def test_a_windows_install_on_wsls_path_is_not_found_in_wsl() -> None:
    """WSL appends Windows's PATH by default, and npm leaves an extensionless shim."""
    out = (
        "/mnt/c/Users/someone/AppData/Roaming/npm/codex\n"
        "/MNT/D/tools/agy\n"
        "/home/someone/.local/bin/claude\n"
    )
    assert ai_terminal.apps_in(out) == ["claude_code"]


def test_the_first_running_distro_with_the_app_is_the_one(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"Ubuntu": "/home/a/.local/bin/agy\n", "Debian": "/usr/bin/agy\n/usr/bin/codex\n"}

    class Done:
        def __init__(self, out: str) -> None:
            self.stdout = out.encode()

    def run(argv: list[str], **_: Any) -> Done:
        assert argv[:3] == ["wsl.exe", "-d", argv[2]] and "-lic" in argv
        return Done(answers[argv[2]])

    monkeypatch.setattr(ai_terminal.subprocess, "run", run)
    assert ai_terminal.find_in_wsl(["Ubuntu", "Debian"]) == {
        "antigravity": "Ubuntu",
        "codex": "Debian",
    }


def test_found_reports_both_sides_for_every_app() -> None:
    found = ai_terminal.Found(windows={"codex": "codex.exe"}, wsl={"antigravity": "Ubuntu"})
    assert found.as_dict() == {
        "claude_code": {"windows": False, "wsl": None},
        "codex": {"windows": True, "wsl": None},
        "antigravity": {"windows": False, "wsl": "Ubuntu"},
    }


@pytest.mark.parametrize(
    ("on_path", "expected"),
    [
        # Installed by the ChatGPT app or OpenAI's installer: the command as shown.
        ("C:\\Users\\someone\\AppData\\Local\\Programs\\OpenAI\\Codex\\bin\\codex.exe", CODEX),
        # npm: codex.ps1 beside codex.cmd; PowerShell would pick the script.
        (
            "C:\\Users\\someone\\AppData\\Roaming\\npm\\codex.CMD",
            f'codex.cmd mcp add upshot -- "{BRIDGE}"',
        ),
        # A .bat shim keeps its own extension.
        ("C:\\tools\\codex.bat", f'codex.bat mcp add upshot -- "{BRIDGE}"'),
        # Found only where its installer leaves it, not on the PATH: the full path.
        (None, f'& "C:\\winget\\codex.exe" mcp add upshot -- "{BRIDGE}"'),
    ],
)
def test_the_windows_line_names_the_program_as_powershell_will_find_it(
    monkeypatch: pytest.MonkeyPatch, on_path: str | None, expected: str
) -> None:
    monkeypatch.setattr(ai_terminal.shutil, "which", lambda program, path=None: on_path)
    assert ai_terminal.windows_line(CODEX, "C:\\winget\\codex.exe", "C:\\fresh") == expected


def test_powershell_types_the_line_and_runs_nothing() -> None:
    argv = ai_terminal.powershell_argv(
        'agy mcp add upshot "C:\\it\'s\\upshot-mcp.exe"', "Upshot - Codex"
    )
    assert argv[:4] == ["powershell.exe", "-NoExit", "-NoProfile", "-Command"]
    script = argv[4]
    assert (
        "PSConsoleReadLine]::Insert('agy mcp add upshot \"C:\\it''s\\upshot-mcp.exe\"')" in script
    )
    assert "-MaxTriggerCount 1" in script and "PowerShell.OnIdle" in script
    assert "EncodedCommand" not in " ".join(argv)


def test_every_quote_powershell_reads_as_a_single_quote_is_doubled() -> None:
    """A profile folder named O’Brien must not end the string early."""
    script = ai_terminal.powershell_argv(
        'agy mcp add upshot "C:\\Users\\O\u2019Brien\\x.exe"', "Upshot - Antigravity"
    )[4]
    assert "O\u2019\u2019Brien" in script
    for quote in "\u2018\u201a\u201b'":
        assert quote * 2 in ai_terminal.powershell_argv(f"a{quote}b", "Upshot - Codex")[4]


def test_wsl_gets_the_line_as_an_argument_never_inside_the_script() -> None:
    line = 'codex mcp add upshot -- "/mnt/c/x/upshot-mcp.exe" --wsl-distro "$WSL_DISTRO_NAME"'
    argv = ai_terminal.wsl_argv(line, "Ubuntu-24.04", "Upshot - Codex")
    assert argv[:6] == ["wsl.exe", "-d", "Ubuntu-24.04", "--cd", "~", "-e"]
    assert argv[-1] == line and line not in argv[-3]
    assert 'read -e -i "$1"' in argv[-3]
    assert ai_terminal.wsl_argv(line, None, "Upshot - Codex")[:3] == ["wsl.exe", "--cd", "~"]


@pytest.mark.skipif(sys.platform == "win32", reason="looks things up on Windows")
def test_nothing_is_looked_up_off_windows() -> None:
    assert ai_terminal.registry_path() is None
    assert ai_terminal.find_on_windows(None) == {}
    assert ai_terminal.running_distros() == []


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows's")
def test_the_path_is_read_again_from_the_registry() -> None:
    fresh = ai_terminal.registry_path()
    assert fresh, "the machine's and the user's PATH, joined"


def test_each_app_has_its_own_title_and_the_terminal_carries_it() -> None:
    titles = {ai_terminal.title(app) for app in ai_terminal.PROGRAMS}
    assert titles == {"Upshot - Claude Code", "Upshot - Codex", "Upshot - Antigravity"}
    assert "WindowTitle = 'Upshot - Codex'" in ai_terminal.powershell_argv("x", "Upshot - Codex")[4]
    assert ai_terminal.wsl_argv("x", "Ubuntu-24.04", "Upshot - Codex")[-2] == "Upshot - Codex"


@pytest.mark.skipif(sys.platform == "win32", reason="lists real windows on Windows")
def test_no_terminal_windows_are_listed_off_windows() -> None:
    from app.window import terminal_windows

    assert terminal_windows("Upshot - Codex") == set()


class _Gone(Exception):
    """What pywin32 raises when a window closed while it was looked at."""


def _fake_win32gui(monkeypatch: pytest.MonkeyPatch, *, list_fails: bool = False) -> None:
    """Three windows: Upshot's terminal, one that closes mid-list, and another app's."""
    import types

    windows = {1: ("CASCADIA_HOSTING_WINDOW_CLASS", "Upshot - Codex"), 3: ("Notepad", "x")}

    def enum(visit: Any, extra: object) -> None:
        if list_fails:
            raise _Gone("EnumWindows")
        for hwnd in (1, 2, 3):
            visit(hwnd, extra)

    def class_name(hwnd: int) -> str:
        if hwnd not in windows:
            raise _Gone("GetClassName")
        return windows[hwnd][0]

    fake = types.SimpleNamespace(
        EnumWindows=enum,
        IsWindowVisible=lambda hwnd: True,
        GetClassName=class_name,
        GetWindowText=lambda hwnd: windows[hwnd][1],
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake)
    monkeypatch.setattr(sys, "platform", "win32")


def test_a_window_closing_mid_list_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.window import terminal_windows

    _fake_win32gui(monkeypatch)
    assert terminal_windows("Upshot - Codex") == {1}


def test_a_list_that_fails_is_empty_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.window import terminal_windows

    _fake_win32gui(monkeypatch, list_fails=True)
    assert terminal_windows("Upshot - Codex") == set()


def test_two_clicks_for_one_app_bring_forward_two_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import window

    monkeypatch.setattr(window, "_CLAIMED", {})
    assert window._claim({11, 12}, now=100.0) == 11
    assert window._claim({11, 12}, now=100.5) == 12
    assert window._claim({11, 12}, now=101.0) is None


def test_a_claim_lasts_only_while_the_clicks_look(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows Terminal may put every new tab into one window: it comes forward each time."""
    from app import window

    monkeypatch.setattr(window, "_CLAIMED", {})
    assert window._claim({11}, now=100.0) == 11
    assert window._claim({11}, now=100.0 + window.CLAIM_S + 1) == 11


def _fake_desktop(
    monkeypatch: pytest.MonkeyPatch, windows: dict[int, str], *, minimized: set[int]
) -> list[tuple[str, int]]:
    """Fake pywin32 for ``terminal_forward``: Upshot's window (99) in front, terminals by
    title, and a record of what was done to which window."""
    import types

    from app import window

    calls: list[tuple[str, int]] = []
    win32gui = types.SimpleNamespace(
        EnumWindows=lambda visit, extra: [visit(hwnd, extra) for hwnd in list(windows)],
        IsWindowVisible=lambda hwnd: True,
        GetClassName=lambda hwnd: "CASCADIA_HOSTING_WINDOW_CLASS",
        GetWindowText=lambda hwnd: windows[hwnd],
        GetForegroundWindow=lambda: 99,
        IsIconic=lambda hwnd: hwnd in minimized,
        ShowWindow=lambda hwnd, how: calls.append(("restore", hwnd)),
        SetForegroundWindow=lambda hwnd: calls.append(("front", hwnd)),
    )
    win32con = types.SimpleNamespace(SW_RESTORE=9, VK_MENU=18, KEYEVENTF_KEYUP=2)
    win32api = types.SimpleNamespace(keybd_event=lambda *args: None)
    for name, module in (("win32gui", win32gui), ("win32con", win32con), ("win32api", win32api)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(window, "_is_ours", lambda gui, hwnd: hwnd == 99)
    monkeypatch.setattr(window, "_CLAIMED", {})
    return calls


def test_a_window_open_before_the_click_never_comes_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It may be one the user kept for their own work, a command half typed in."""
    from app import window

    calls = _fake_desktop(monkeypatch, {5: "Upshot - Codex"}, minimized=set())
    assert window.terminal_forward("Upshot - Codex", {5}, window_s=0.2) == "no terminal window"
    assert calls == []


def test_a_minimized_new_terminal_is_restored_then_brought_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import window

    calls = _fake_desktop(monkeypatch, {5: "Upshot - Codex", 6: "Upshot - Codex"}, minimized={6})
    outcome = window.terminal_forward("Upshot - Codex", {5}, window_s=0.2)
    assert outcome == "brought the terminal forward"
    assert calls == [("restore", 6), ("front", 6)]


def test_the_mcp_json_row_counts_any_of_its_apps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """VS Code by its installer's folder, Cursor by its command on the PATH: either is
    enough to list the row with the apps found."""
    code = tmp_path / "Programs" / "Microsoft VS Code" / "Code.exe"
    code.parent.mkdir(parents=True)
    code.write_text("")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        ai_terminal.os.path,
        "expandvars",
        lambda text: text.replace("%LOCALAPPDATA%", str(tmp_path)).replace("\\", "/"),
    )
    monkeypatch.setattr(ai_terminal, "registry_path", lambda: "C:\\bin")
    monkeypatch.setattr(
        ai_terminal.shutil,
        "which",
        lambda program, path=None: "C:\\bin\\cursor.cmd" if program == "cursor" else None,
    )
    assert ai_terminal.find_mcp_json_apps() == ["VS Code", "Cursor"]
    monkeypatch.setattr(ai_terminal.shutil, "which", lambda program, path=None: None)
    code.unlink()
    assert ai_terminal.find_mcp_json_apps() == []


def test_the_mcp_json_row_off_windows_and_in_the_e2e_server() -> None:
    assert ai_terminal.find_mcp_json_apps(test=True) == ["VS Code"]
    if sys.platform != "win32":
        assert ai_terminal.find_mcp_json_apps() == []


def test_the_terminal_does_not_inherit_upshots_hidden_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upshot sets CUDA_VISIBLE_DEVICES="" for itself without a usable GPU; the user's
    terminal must still see the GPU. A value of the user's own stays."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    env = ai_terminal.terminal_env("C:\\fresh")
    assert "CUDA_VISIBLE_DEVICES" not in env and env["PATH"] == "C:\\fresh"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert ai_terminal.terminal_env(None)["CUDA_VISIBLE_DEVICES"] == "1"
