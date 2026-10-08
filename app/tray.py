"""The tray icon (pystray). PyInstaller's entry point, and the app's main thread.

pystray needs the main thread on Windows, so everything else is spawned from here.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

from app import actions, brand, window
from app.instance import ALREADY_RUNNING, SingleInstance
from app.log import get, setup
from app.services import Services
from app.tray_state import Action, AppState, IconColor, IconSpec, MenuItem, RecorderState, icon_for

log = get(__name__)

ICON_SIZE = 64
APP_USER_MODEL_ID = "Upshot.App"


def register_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Toasts carry the app's name and persist in Action Center only with this set."""
    try:  # pragma: no cover - Windows only
        import ctypes

        # `windll` exists only on Windows, where the ignore is in turn unused.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined,unused-ignore]
            app_id
        )
        return True
    except Exception:
        return False


#: The idle teal on a dark taskbar: the brand's dark-theme accent, since the light-theme
#: one (IconColor.IDLE) is too dark to read there. From frontend/src/tokens.css.
IDLE_ON_DARK = (0x3D, 0xBD, 0xB0)


def dark_taskbar() -> bool:
    """Whether Windows draws the taskbar dark (the default on Windows 11)."""
    try:  # pragma: no cover - Windows only
        import importlib

        winreg: Any = importlib.import_module("winreg")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 0)
    except Exception:
        return False


def render_icon(spec: IconSpec, *, dark: bool = False) -> Any:
    """The mark in the state's colour, with a corner badge when needed.

    Every decision except the drawing still belongs to :func:`app.tray_state.icon_for`
    and the geometry belongs to :mod:`app.brand`; this function only puts the two
    together.
    """
    rgb = IDLE_ON_DARK if dark and spec.color is IconColor.IDLE else spec.rgb
    return brand.render(ICON_SIZE, rgb, badge=spec.badge)


class TrayApp:
    """Owns the icon; every decision comes from :func:`app.tray_state.icon_for`."""

    def __init__(
        self,
        services: Services,
        *,
        on_action: Callable[[Action], None] | None = None,
    ) -> None:
        self.services = services
        self.on_action = on_action or self.dispatch
        self.state = AppState(detector_mode=str(services.config.get("detection.mode", "shadow")))
        self.icon: Any = None
        self._stop = threading.Event()
        #: Quit from the tray menu, not by the installer or the uninstaller: only such a
        #: quit may install a ready update (D87).
        self.quit_by_user = False

    def open_link(self) -> str:
        return self.services.auth.link(self.services.config.server_port)

    # -- state -------------------------------------------------------------

    def observe(self) -> AppState:
        recorder = self.services.recorder
        worker = self.services.worker
        if recorder is None or not recorder.committed:
            recorder_state = (
                RecorderState.ARMED if (recorder and recorder.armed) else RecorderState.IDLE
            )
        elif recorder.paused:
            recorder_state = RecorderState.PAUSED
        else:
            recorder_state = RecorderState.RECORDING
        # Busy with file transcriptions too (D86); a failed one is no meeting error, so
        # "failed" below stays the meetings' own count.
        scheduler = getattr(self.services, "scheduler", None)
        depth = scheduler.depth() if scheduler is not None else self.services.queue.depth()
        failed = self.services.queue.counts().get("failed", 0)
        transcribing = None
        if depth and recorder_state is RecorderState.IDLE:
            from app.transcription.active import running_progress

            transcribing = running_progress(self.services)
        self.state = AppState(
            recorder=recorder_state,
            processing=depth > 0 and recorder_state is RecorderState.IDLE,
            error=failed > 0,
            queue_depth=depth,
            transcribing=transcribing,
            meeting_title=self._title(),
            detector_mode=str(self.services.config.get("detection.mode", "shadow")),
            ending=bool(recorder is not None and recorder.holding),
            worker_alive=worker is None or worker.is_alive(),
            update_ready=self._update_ready(),
        )
        return self.state

    def _update_ready(self) -> str | None:
        installer = self.services.installer
        if installer is None or not installer.can_install:
            return None
        ready = installer.updates.ready()
        return str(ready["version"]) if ready else None

    def _title(self) -> str | None:
        recorder = self.services.recorder
        if recorder is None or recorder.meeting_id is None:
            return None
        meeting = self.services.dao.get_meeting(recorder.meeting_id)
        return meeting.title if meeting else None

    def spec(self) -> IconSpec:
        return icon_for(self.observe())

    def refresh(self) -> IconSpec:
        spec = self.spec()
        if self.icon is not None:  # pragma: no cover - needs a desktop
            self.icon.icon = render_icon(spec, dark=dark_taskbar())
            self.icon.title = spec.tooltip
            self.icon.menu = self._menu(spec)
            self.icon.update_menu()
        return spec

    # -- actions -----------------------------------------------------------

    def dispatch(self, action: Action) -> None:
        """The menu and the toast buttons take the same path the UI does."""
        services = self.services
        if action is Action.START and services.recorder is not None:
            meeting = services.meetings.create(source="manual")
            services.recorder.start(meeting.path, meeting.id)
            services.recorder.start_thread()
            services.events.publish("recorder", state="recording", meeting_id=meeting.id)
        elif action is Action.STOP and services.recorder is not None:
            meeting_id = services.recorder.meeting_id
            result = services.recorder.stop()
            if meeting_id:
                services.meetings.finish(
                    meeting_id, duration_s=round(result.total_duration_ms / 1000)
                )
            services.events.publish("recorder", state="idle", meeting_id=meeting_id)
        elif action is Action.PAUSE and services.recorder is not None:
            if services.recorder.paused:
                services.recorder.resume()
            else:
                services.recorder.pause()
        elif action is Action.OPEN:
            # A fresh one-time link on every click. The single link minted at startup
            # was spent by the first browser, so a second browser profile opened from
            # here used to be refused with nowhere to get another.
            self.show_window()
        elif action is Action.QUIT:
            self.quit_by_user = True
            self.stop()
        elif action is Action.FEEDBACK:
            # The window, with the feedback form open on it (D87).
            how = window.open_window(self.open_link() + "&feedback=1")
            log.info("feedback window: %s", how)
        elif action is Action.UPDATE and services.installer is not None:
            # "Restart to update": the installer starts, the app quits, and the installer
            # starts it again (D87). Refused only while recording; the item is greyed then.
            try:
                services.installer.install_now()
            except (LookupError, RuntimeError) as exc:
                log.info("restart to update refused: %s", exc)
        self.refresh()

    def show_window(self) -> str:
        """Upshot's window (app/window.py): brought forward if open, opened if not."""
        # First-run setup opens maximized: its steps are laid out for a full screen.
        how = window.open_window(self.open_link(), maximized=self.setup_pending())
        log.info("window: %s", how)
        return how

    def setup_pending(self) -> bool:
        return self.services.config.get("setup.done", True) is False

    def on_start(self, *, background: bool) -> bool:
        """What a start shows. True when it opened the window.

        Started by the user (the Start menu, the installer's "Launch Upshot") the window
        opens: the app was asked for. Started at sign-in (``--background``) it stays in
        the tray, unless first-run setup is still to be done, which nobody would find
        behind the icon.
        """
        # A window still open now belongs to a run that has ended (a crash, or an upgrade
        # that stopped the app): its page is dead. Close it rather than focus it (D74).
        stale = window.close_all()
        if stale:
            log.info("closed %d Upshot window(s) left from an earlier run", stale)
        if background and not self.setup_pending():
            return False
        self.show_window()
        return True

    # -- lifecycle ---------------------------------------------------------

    def _menu(self, spec: IconSpec) -> Any:  # pragma: no cover - needs pystray
        import pystray

        def build(item: MenuItem) -> Any:
            return pystray.MenuItem(
                item.label,
                lambda *_args, action=item.action: self.on_action(action),
                enabled=item.enabled,
                # The default item is what a left-click on the icon does (Windows).
                default=item.default,
                checked=(lambda *_a, value=item.checked: bool(value))
                if item.checked is not None
                else None,
            )

        return pystray.Menu(*[build(item) for item in spec.menu])

    def run(self) -> None:  # pragma: no cover - needs a desktop
        import pystray

        register_app_user_model_id()
        spec = self.spec()
        self.icon = pystray.Icon(
            "upshot", render_icon(spec, dark=dark_taskbar()), spec.tooltip, self._menu(spec)
        )
        threading.Thread(target=self._poll, name="tray-poll", daemon=True).start()
        self.icon.run()

    def _poll(self) -> None:  # pragma: no cover - needs a desktop
        while not self._stop.wait(2.0):
            self.refresh()

    def stop(self) -> None:
        # The window would otherwise stay open on a dead page (D74).
        window.close_all()
        self._stop.set()
        if self.icon is not None:  # pragma: no cover - needs a desktop
            self.icon.stop()


def run_link(link: str, home: Any = None) -> int | None:
    """Carry out an ``upshot:`` link. None means "start Upshot normally" instead.

    Run from a toast, this process has the right to take the foreground and the tray one
    has not, so it opens a page itself. Everything else it hands to the running instance
    and exits without a window: pressing Start recording must not move the user's focus.
    """
    from app import paths
    from app.instance import fresh_link, recorded_port

    parsed = actions.parse(link)
    if parsed is None:
        log.warning("ignoring a link that is not one of Upshot's: %r", link)
        return 2
    # Which button it was, in words a reader of the log can match to the screen: "Join
    # and record" and "Start recording" both start a recording, and only the join tells
    # them apart (machine B, D78).
    log.info("notification button: %s", actions.describe(parsed))
    home = home or paths.app_home()
    port = recorded_port(home)
    if parsed.action in actions.PAGES:
        url = fresh_link(port, home, actions.page(parsed)) if port else None
        if url is None:
            return None  # not running: an ordinary start opens the window
        window.open_window(url)
        return 0
    # "Join and record": the meeting opens in the browser, where the user wants to be
    # (D76). Otherwise the focus goes back to the window the button was pressed over (D72).
    join = actions.join_url(parsed)
    if join:
        import webbrowser

        webbrowser.open(join)
        log.info("opened the meeting link in the browser (%s)", actions.host(join))
        back = None
    else:
        back = window.give_focus_back()
    if back:
        log.info("toast action %s: focus back to %r", parsed.action, back)
    if port is None:
        log.warning("toast action %s: Upshot is not running", parsed.action)
        return 1
    return 0 if actions.forward(parsed, port, home) else 1


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entry point
    """The frozen executable's entry point.

    ``upshot.exe --selftest imports`` is the PyInstaller hidden-import tripwire:
    it imports every ``app.*`` module *inside the freeze*, where a missing hidden import
    is the classic failure.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if sys.stderr is None:
        # Windowed: no stderr for the model download's progress bars to write to. Set
        # before huggingface_hub is imported, which reads it once.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    if "--quit" in arguments:
        # The installer and the uninstaller close a running copy this way (app/instance.py).
        from app.instance import request_quit

        return 0 if request_quit() else 1
    if "--selftest" in arguments:
        from app.selftest import main as selftest_main

        index = arguments.index("--selftest")
        return selftest_main(arguments[index + 1 :] or ["all"])
    if "--toast" in arguments:
        # How a frozen build shows a notification: the toast runs as its own process
        # (app/notify_toast.py), and in a freeze this executable is the only interpreter.
        from app.notify_toast import main as toast_main

        index = arguments.index("--toast")
        return toast_main(arguments[index + 1 :])
    if "--prepare" in arguments:
        # The installer's download step: the speech model and, with a suitable NVIDIA
        # GPU, the CUDA libraries (app/prepare.py). The installer reads its progress file.
        from app.prepare import main as prepare_main

        setup()
        index = arguments.index("--prepare")
        return prepare_main(arguments[index + 1 :])
    if "--bootstrap" in arguments:
        from app.bootstrap import run as bootstrap_run

        setup()
        # No logon task here: autostart is the installer's Startup-folder shortcut, which
        # Windows' Startup apps list can switch off; a task would ignore that. Creating an
        # ONLOGON task also needs admin, so on a standard account it failed and took the
        # whole first run down with it (machine B).
        # The installer passes --setup-again: setup runs after every install (D69).
        report = bootstrap_run(register_task=False, setup_again="--setup-again" in arguments)
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.ok else 1
    link = actions.link_argument(arguments)
    if link:
        # A toast button or a click on a notification (app/actions.py, D70).
        setup()
        code = run_link(link)
        if code is not None:
            return code
    setup()
    from app.version import build_info

    info = build_info()
    log.info("Upshot %s (commit %s, built %s)", info.version, info.commit, info.built)
    guard = SingleInstance()
    if not guard.acquire():
        from app.instance import show_running

        opened = show_running()
        log.info("Upshot is already running%s", "; opened its page" if opened else "")
        return ALREADY_RUNNING
    from app.config import Config
    from app.main import create_app, start_background
    from app.server import LocalServer, choose_port
    from app.services import build

    config = Config.load()
    config.set("server.port", choose_port(config.server_host, config.server_port))
    services = build(config)
    from app import paths
    from app.diagnostics import native
    from app.diagnostics.reporter import set_active

    # Crash reports (D87): first what a crash of the last run left, then this run's watch.
    # Only the instance that owns the single-instance lock does this.
    set_active(services.reporter)
    crash = native.found(paths.app_home())
    if crash is not None and services.reporter is not None:
        services.reporter.report_native(crash["frames"], version=crash.get("version"))
    native.arm(paths.app_home(), version=info.version)
    app = create_app(services)
    server = LocalServer(app, host=services.config.server_host, port=services.config.server_port)
    server.start()
    # The same background work `python -m app.main` starts. Started the worker and
    # nothing else before, so the tray build — the one the installer runs — had no
    # detector at all, whatever the mode said.
    start_background(services)
    from app.api.security import write_launcher_key
    from app.instance import record_port, watch_quit

    write_launcher_key(services.auth, paths.app_home())
    record_port(server.bound_port)
    tray = TrayApp(services)
    watch_quit(tray.stop)
    installer = services.installer
    if installer is not None:
        # Once the update installer has started, the app quits so it can be replaced.
        installer.quit = tray.stop
    # The sign-in shortcut passes --background (packaging/installer.iss). After an
    # automatic update the installer passes --background --after-update; the window
    # comes back only if it was open when the update began (D87).
    background = "--background" in arguments
    if "--after-update" in arguments and installer is not None:
        outcome = installer.outcome or {}
        background = not outcome.get("window_open", False)
    tray.on_start(background=background)
    try:
        tray.run()
    finally:
        if tray.quit_by_user and installer is not None:
            # The user quit from the tray: a ready update installs now, if it may.
            installer.at_quit()
        server.stop()
        services.close()
        # A clean quit: nothing to report at the next start.
        native.disarm(paths.app_home())
        guard.release()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    # PyInstaller runs this file as the script, as __main__. Without these lines the
    # frozen upshot.exe defined main() and exited 0 at once: no tray, no server, and
    # every --selftest "passed" without running (the first freeze, machine A, 2026-09-23).
    import multiprocessing

    # First: a child process the frozen exe starts for the CPU language classifier
    # (app/asr/classify.py) arrives here and must run that, not the app.
    multiprocessing.freeze_support()
    sys.exit(main())
