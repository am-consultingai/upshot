"""No Windows error dialogs from the programs Upshot starts.

When a program fails before its own code runs (a missing DLL, or 0xC0000017: no memory
to start it), Windows shows a modal "Application Error" dialog and the program waits for
someone to press OK. ``CREATE_NO_WINDOW`` hides the console, not that dialog. Upshot runs
ffmpeg once for every file it reads, so a Windows that could not start ffmpeg showed a
new dialog about every minute, for days, and each ffmpeg hung until Upshot's timeout.

``SEM_FAILCRITICALERRORS`` makes the system return the error to the program instead, so
the child ends at once with its NTSTATUS code. A process's error mode is inherited by
the processes it creates, so setting it once at startup covers ffmpeg, nvidia-smi and
every AI CLI. It also reaches what the user sees (the browser window when Upshot starts
the browser, the update installer): only their critical-error dialogs are affected, and
those programs report such errors themselves. ``SEM_NOGPFAULTERRORBOX`` is deliberately
not set: it would also switch off Windows Error Reporting for Upshot's own crashes.
"""

from __future__ import annotations

import sys

#: Critical errors (a failed process start included) go to the program, not a dialog.
SEM_FAILCRITICALERRORS = 0x0001
#: No dialog when a file cannot be opened (a removed drive, for example).
SEM_NOOPENFILEERRORBOX = 0x8000
QUIET = SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX


def quiet_child_errors() -> int | None:
    """Add :data:`QUIET` to this process's error mode, keeping what is already set.
    The mode before, or None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    before = int(kernel32.GetErrorMode())
    kernel32.SetErrorMode(before | QUIET)
    return before
