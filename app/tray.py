"""The tray icon (pystray). PyInstaller's entry point, and the app's main thread.

pystray needs the main thread on Windows, so everything else is spawned from here.
"""

from __future__ import annotations

import json
import sys
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

from app import brand
from app.instance import ALREADY_RUNNING, SingleInstance
from app.log import get, setup
from app.services import Services
from app.tray_state import Action, AppState, IconSpec, MenuItem, RecorderState, icon_for

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


def render_icon(spec: IconSpec) -> Any:
    """The mark in the state's colour, with a corner badge when needed.

    Every decision except the drawing still belongs to :func:`app.tray_state.icon_for`
    and the geometry belongs to :mod:`app.brand`; this function only puts the two
    together.
    """
    return brand.render(ICON_SIZE, spec.rgb, badge=spec.badge)


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
        depth = self.services.queue.depth()
        failed = self.services.queue.counts().get("failed", 0)
        self.state = AppState(
            recorder=recorder_state,
            processing=depth > 0 and recorder_state is RecorderState.IDLE,
            error=failed > 0,
            queue_depth=depth,
            meeting_title=self._title(),
            detector_mode=str(self.services.config.get("detection.mode", "shadow")),
            detector_muted=bool(self.services.extras.get("detector_muted", False)),
            worker_alive=worker is None or worker.is_alive(),
        )
        return self.state

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
            self.icon.icon = render_icon(spec)
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
            webbrowser.open(self.open_link())
        elif action is Action.MUTE_HOUR:
            muted = not bool(services.extras.get("detector_muted", False))
            services.extras["detector_muted"] = muted
            services.events.publish("detector", muted=muted)
        elif action is Action.QUIT:
            self.stop()
        self.refresh()

    # -- lifecycle ---------------------------------------------------------

    def _menu(self, spec: IconSpec) -> Any:  # pragma: no cover - needs pystray
        import pystray

        def build(item: MenuItem) -> Any:
            return pystray.MenuItem(
                item.label,
                lambda *_args, action=item.action: self.on_action(action),
                enabled=item.enabled,
                checked=(lambda *_a, value=item.checked: bool(value))
                if item.checked is not None
                else None,
            )

        return pystray.Menu(*[build(item) for item in spec.menu])

    def run(self) -> None:  # pragma: no cover - needs a desktop
        import pystray

        register_app_user_model_id()
        spec = self.spec()
        self.icon = pystray.Icon("upshot", render_icon(spec), spec.tooltip, self._menu(spec))
        threading.Thread(target=self._poll, name="tray-poll", daemon=True).start()
        self.icon.run()

    def _poll(self) -> None:  # pragma: no cover - needs a desktop
        while not self._stop.wait(2.0):
            self.refresh()

    def stop(self) -> None:
        self._stop.set()
        if self.icon is not None:  # pragma: no cover - needs a desktop
            self.icon.stop()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entry point
    """The frozen executable's entry point.

    ``upshot.exe --selftest imports`` is the PyInstaller hidden-import tripwire:
    it imports every ``app.*`` module *inside the freeze*, where a missing hidden import
    is the classic failure.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
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
    if "--bootstrap" in arguments:
        from app.bootstrap import run as bootstrap_run

        setup()
        # No logon task here: autostart is the installer's opt-in "Start Upshot when I
        # sign in" shortcut. A task would start the app even when the user unticked that,
        # and creating an ONLOGON task needs admin, so on a standard account it failed
        # and took the whole first run down with it (machine B).
        report = bootstrap_run(register_task=False)
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.ok else 1
    setup()
    guard = SingleInstance()
    if not guard.acquire():
        log.error("Upshot is already running")
        return ALREADY_RUNNING
    from app.main import create_app, start_background
    from app.server import LocalServer
    from app.services import build

    services = build()
    app = create_app(services)
    server = LocalServer(app, host=services.config.server_host, port=services.config.server_port)
    server.start()
    # The same background work `python -m app.main` starts. Started the worker and
    # nothing else before, so the tray build — the one the installer runs — had no
    # detector at all, whatever the mode said.
    start_background(services)
    from app import paths
    from app.api.security import write_launcher_key

    write_launcher_key(services.auth, paths.app_home())
    tray = TrayApp(services)
    try:
        tray.run()
    finally:
        server.stop()
        services.close()
        guard.release()
    return 0
