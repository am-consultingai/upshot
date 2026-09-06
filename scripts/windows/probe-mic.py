"""Probe the microphone ConsentStore — the signal DETECTION.md §2 builds start/stop on.

Standalone on purpose: it imports nothing from ``app``, so it runs under any Windows
Python that has ``pyaudiowpatch``, including the launcher's venv at
``%LOCALAPPDATA%\\meeting-agent-win\\venv\\Scripts\\python.exe``.

    python probe-mic.py            # who holds the microphone right now
    python probe-mic.py watch      # print every acquire/release edge as it happens
    python probe-mic.py devices    # which capture paths register at all

``watch`` is the one to run while joining, muting and leaving a real meeting: it is the
only way to see whether mute releases the stream (it must not) and whether Leave does.

``devices`` answers what a first run of this found on the author's machine: the
ConsentStore follows the **host API**, not the device. An MME (legacy ``waveIn``) capture
of a physical microphone is invisible to it; DirectSound and WASAPI captures of the same
microphone both register. Loopback endpoints never register, which is correct — they are
render-side and are not the microphone capability.
"""

from __future__ import annotations

import sys
import time
import winreg

CONSENT_ROOT = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)
NON_PACKAGED = "NonPackaged"
IN_USE = 0  # LastUsedTimeStop == 0 means "open right now"

SETTLE_S = 3.0  # the registry lags the stream, but by well under this


def _holders_under(key, *, packaged: bool) -> list[str]:
    found: list[str] = []
    index = 0
    while True:
        try:
            name = winreg.EnumKey(key, index)
        except OSError:
            break
        index += 1
        with winreg.OpenKey(key, name) as sub:
            try:
                stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
            except OSError:
                continue
            if int(stop) == IN_USE:
                found.append(name if packaged else name.replace("#", "\\"))
    return found


def holders() -> list[str]:
    """Every app with the microphone open, exactly as app/detect/registry.py reads it."""
    found: list[str] = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_ROOT) as root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            with winreg.OpenKey(root, name) as sub:
                found.extend(_holders_under(sub, packaged=name != NON_PACKAGED))
    return sorted(found)


def now() -> str:
    return time.strftime("%H:%M:%S")


def show() -> None:
    current = holders()
    print(f"[{now()}] {len(current)} holder(s)")
    for item in current:
        print(f"    {item}")


def watch() -> None:
    """Print edges, not state. Join a meeting, mute, unmute, leave — see what fires."""
    print("watching the ConsentStore. Ctrl+C to stop.\n")
    previous = set(holders())
    for item in sorted(previous):
        print(f"[{now()}] already holding  {item}")
    while True:
        time.sleep(0.5)
        current = set(holders())
        for item in sorted(current - previous):
            print(f"[{now()}] ACQUIRED         {item}")
        for item in sorted(previous - current):
            print(f"[{now()}] RELEASED         {item}")
        previous = current


def devices() -> None:
    """Open every input endpoint on every host API and report whether Windows noticed."""
    import pyaudiowpatch as pyaudio

    audio = pyaudio.PyAudio()
    print(f"{'registers':<11}{'host api':<24}device")
    try:
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if int(info["maxInputChannels"]) < 1:
                continue
            api = str(audio.get_host_api_info_by_index(int(info["hostApi"]))["name"])
            name = str(info["name"])
            before = set(holders())
            try:
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=int(info["defaultSampleRate"]),
                    input=True,
                    input_device_index=index,
                    frames_per_buffer=1024,
                )
            except Exception as exc:  # a busy or unusable endpoint is not a result
                print(f"{'skipped':<11}{api:<24}{name}  ({exc})")
                continue
            time.sleep(SETTLE_S)
            registered = bool(set(holders()) - before)
            stream.stop_stream()
            stream.close()
            time.sleep(SETTLE_S)
            print(f"{'YES' if registered else 'no':<11}{api:<24}{name}")
    finally:
        audio.terminate()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "show"
    {"show": show, "watch": watch, "devices": devices}[mode]()
