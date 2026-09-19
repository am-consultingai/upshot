"""Show one Windows toast, then exit. A process of its own, on purpose.

WinRT objects belong to the COM apartment of the thread that created them, and the
server's request handlers run on interchangeable, short-lived threadpool threads. Every
notification failure this project has had came from that mismatch: first
``RPC_E_WRONG_THREAD``, then "object is not connected to server", and on 2026-09-18 an
access violation inside ``_winrt_windows_data_xml_dom`` that killed the whole process the
moment a recording was stopped — after the meeting had been filed, so no Python exception
could be raised and nothing reached the log.

A separate process ends the class: the toast gets a clean main thread that nothing else
shares, and if the notification stack faults it takes only this process with it. The
recording is already on disk by then.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

APP_ID = "Upshot.App"
#: Windows takes the notification asynchronously. Leaving immediately can drop it, and a
#: fraction of a second in a process nothing waits on costs nothing.
SETTLE_S = 0.5


def show(payload: dict[str, Any]) -> None:
    """Raises whatever the notification stack raises; ``main`` decides what that means."""
    from windows_toasts import InteractableWindowsToaster, Toast, ToastButton, WindowsToaster

    buttons = list(payload.get("buttons") or [])
    # WindowsToaster drops actions with a warning, so anything with buttons has to go
    # through the interactable one (DETECTION.md §6: the buttons are the mechanism).
    builder = InteractableWindowsToaster if buttons else WindowsToaster
    toaster = builder(str(payload.get("app_id") or APP_ID))
    toast = Toast()
    toast.text_fields = [str(payload.get("title", "")), str(payload.get("body", ""))]
    for button in buttons:
        toast.AddAction(ToastButton(str(button["label"]), arguments=str(button["action"])))
    toaster.show_toast(toast)
    time.sleep(SETTLE_S)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("usage: python -m app.notify_toast '<json>'", file=sys.stderr)
        return 2
    try:
        payload = json.loads(arguments[0])
    except ValueError as exc:
        print(f"bad payload: {exc}", file=sys.stderr)
        return 2
    try:
        show(payload)
    except Exception as exc:
        if payload.get("buttons"):
            # An interactable toaster needs a registered AUMID and is not available
            # everywhere. A notification without its buttons still beats none.
            print(f"buttons unavailable: {exc}", file=sys.stderr)
            try:
                show({**payload, "buttons": []})
            except Exception as plain:
                print(f"toast failed: {plain}", file=sys.stderr)
                return 1
            return 0
        print(f"toast failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
