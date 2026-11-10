"""Importing an existing recording (DESIGN.md §7, "Imported").

The file becomes ordinary chunk files on the ``them`` track, so an imported meeting runs
exactly the same pipeline as a recorded one — single-track, so speakers come from the LLM
rather than from the track split.
"""

from __future__ import annotations

import errno
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.audio.writer import ChunkRecord, ChunkWriter
from app.config import Config
from app.log import get

log = get(__name__)

WAV_SUFFIXES = {".wav", ".wave"}


class UnsupportedAudio(ValueError):
    """The file cannot be read: ffmpeg ran and refused it (not audio or video, no audio
    stream, too slow to convert), or it is not a WAV and a source checkout has no ffmpeg."""


class FfmpegBroken(RuntimeError):
    """The ffmpeg Upshot would run can never run here (not a program, a missing DLL, the
    wrong architecture). Waiting will not fix it: Upshot needs reinstalling, or
    ``ffmpeg_path`` correcting."""


class FfmpegFailed(RuntimeError):
    """ffmpeg itself did not run: it could not start, or the system ended it. This says
    nothing about the file, so a caller tries again later instead of refusing it (a
    Windows that could not start ffmpeg had 2,500 good uploads refused as "not audio")."""


#: How long a program should wait before sending a file again when ffmpeg did not run.
FFMPEG_RETRY_AFTER_S = 60


#: STATUS_NO_MEMORY: Windows could not set the process up, whatever it had printed. Not
#: always the machine's memory: a ``PATH`` past 32,767 characters, inherited from Upshot,
#: ends ffmpeg the same way. The log line of a hold says how much memory was left.
NO_MEMORY = 0xC0000017

#: Failures to run ffmpeg in a row before Upshot stops starting it for a while
#: (z8tj1hwjvv): a Windows that cannot start ffmpeg could not start the next one either.
FFMPEG_HOLD_AFTER = 3
#: How long it waits then, the same as it tells a program to wait before sending again.
FFMPEG_HOLD_S = float(FFMPEG_RETRY_AFTER_S)
#: After a hold, one ffmpeg is tried; the others wait this long for it to fail. A run
#: that has not failed by then has started, so another may try.
FFMPEG_TRIAL_S = 60.0
#: The notice is shown at most this often, however often ffmpeg starts and fails again.
FFMPEG_NOTICE_S = 1800.0


def commit_memory() -> tuple[int, int] | None:
    """Windows' commit charge left and its limit, in bytes (RAM plus the page file): what
    a program needs to start, and what was used up when ffmpeg could not. None elsewhere,
    or when Windows does not say."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPageFile), int(status.ullTotalPageFile)


@dataclass(frozen=True)
class FfmpegHold:
    """Upshot stopped starting ffmpeg: what a notice says about it."""

    failures: int
    error: str
    #: Windows' commit charge left and its limit, in bytes, when it says.
    memory: tuple[int, int] | None


class FfmpegHealth:
    """Whether ffmpeg has been running, across every file and caller (z8tj1hwjvv).

    After :data:`FFMPEG_HOLD_AFTER` failures to run it in a row, Upshot starts no ffmpeg
    for :data:`FFMPEG_HOLD_S`: each caller gets :class:`FfmpegFailed` at once, as it would
    have after launching one, and ``on_hold`` is told, at most once in
    :data:`FFMPEG_NOTICE_S`, so someone hears of it. Then one caller at a time tries an
    ffmpeg (a trial, given :data:`FFMPEG_TRIAL_S` to show it failed), the others still
    waiting: a failure holds again, a run that did its work ends it.

    A run counts as working only when it started after the last failure: a conversion
    that began before memory ran out says nothing about whether ffmpeg starts now. A file
    ffmpeg refuses counts as a run: ffmpeg works.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self.failures = 0
        self.error = ""
        self._held_until: float | None = None
        #: While a trial runs after a hold, the others wait until then.
        self._trial_until: float | None = None
        #: Bumped at every failure: a run started before one does not end the streak.
        self._serial = 0
        self._noticed_at: float | None = None
        #: The ``/info`` recheck: one at a time, and not often (begin_recheck).
        self._rechecking = False
        self._recheck_trial = False
        self._rechecked_at: float | None = None
        self.on_hold: Callable[[FfmpegHold], None] | None = None

    def held(self) -> bool:
        with self._lock:
            return self._wait_left_locked(self._clock()) > 0

    def _wait_left_locked(self, now: float) -> float:
        if self._held_until is None:
            return 0.0
        if now < self._held_until:
            return self._held_until - now
        if self._trial_until is not None and now < self._trial_until:
            return self._trial_until - now
        return 0.0

    def check(self, source: Path) -> int:
        """Raise :class:`FfmpegFailed` without starting ffmpeg while the hold lasts, or
        while another caller's trial runs; else claim the trial if there is a hold. The
        token to hand ``worked`` when this run has done its work."""
        with self._lock:
            now = self._clock()
            left = self._wait_left_locked(now)
            if left <= 0:
                if self._held_until is not None:
                    self._trial_until = now + FFMPEG_TRIAL_S
                return self._serial
            failures, error = self.failures, self.error
        raise FfmpegFailed(
            f"ffmpeg could not run to read {source.name}: it failed {failures} times in a "
            f"row ({error}), so Upshot waits {left:.0f} s before starting it again"
        )

    def failed(self, error: str) -> None:
        with self._lock:
            now = self._clock()
            self._serial += 1
            self.failures += 1
            self.error = error
            if self.failures < FFMPEG_HOLD_AFTER:
                return
            self._held_until = now + FFMPEG_HOLD_S
            self._trial_until = None
            failures = self.failures
            notify = None
            if self._noticed_at is None or now - self._noticed_at >= FFMPEG_NOTICE_S:
                self._noticed_at = now
                notify = self.on_hold
        memory = commit_memory()
        log.warning(
            "ffmpeg failed to run %d times in a row (%s); no ffmpeg is started for %.0f s%s",
            failures,
            error,
            FFMPEG_HOLD_S,
            "" if memory is None else
            f"; Windows memory left: {memory[0] / 2**30:.1f} of {memory[1] / 2**30:.1f} GB",
        )  # fmt: skip
        if notify is not None:
            try:
                notify(FfmpegHold(failures, error, memory))
            except Exception:  # a notice must never turn into the caller's error
                log.exception("ffmpeg hold: the notice failed")

    def worked(self, token: int) -> None:
        """A run that started with ``token`` (from ``check``) did ffmpeg's work."""
        with self._lock:
            if token != self._serial or self.failures == 0:
                return  # started before the last failure, or nothing to end
            failures, held = self.failures, self._held_until is not None
            self.failures = 0
            self.error = ""
            self._held_until = None
            self._trial_until = None
        if held:
            log.info("ffmpeg runs again, after %d failures in a row", failures)

    def begin_recheck(self) -> int | None:
        """Claim the one ``ffmpeg -version`` that tells whether ffmpeg starts again: only
        with failures on record, no hold or trial in the way, none running and none in
        the last :data:`FFMPEG_RECHECK_EVERY_S`. During a hold it is the trial. The token
        for ``worked``, or None."""
        with self._lock:
            now = self._clock()
            if self.failures == 0 or self._rechecking or self._wait_left_locked(now) > 0:
                return None
            last = self._rechecked_at
            if last is not None and now - last < FFMPEG_RECHECK_EVERY_S:
                return None
            self._rechecking = True
            self._rechecked_at = now
            self._recheck_trial = self._held_until is not None
            if self._recheck_trial:
                self._trial_until = now + FFMPEG_TRIAL_S
            return self._serial

    def end_recheck(self, token: int) -> None:
        """The recheck with ``token`` is over. A trial it claimed and that nothing has
        ended since (``worked`` or ``failed``) ends here: no upload waits on a check that
        found no answer."""
        with self._lock:
            self._rechecking = False
            if self._recheck_trial and token == self._serial:
                self._trial_until = None
            self._recheck_trial = False

    def state(self) -> dict[str, object]:
        """For ``/info``: whether ffmpeg is held, and why."""
        with self._lock:
            return {
                "ok": self.failures == 0,
                "held": self._wait_left_locked(self._clock()) > 0,
                "failures_in_a_row": self.failures,
                "error": self.error or None,
            }

    def reset(self) -> None:
        """Forget every failure and notice: a clean start, for tests."""
        with self._lock:
            self.failures = 0
            self.error = ""
            self._held_until = None
            self._trial_until = None
            self._noticed_at = None
            self._rechecking = False
            self._recheck_trial = False
            self._rechecked_at = None


#: The process's one record: every caller runs the same ffmpeg on the same Windows.
HEALTH = FfmpegHealth()


#: ``ffmpeg -version`` answers at once; one still running after this has started.
FFMPEG_RECHECK_TIMEOUT_S = 30
#: The least time between two rechecks, however often ``/info`` is asked.
FFMPEG_RECHECK_EVERY_S = float(FFMPEG_RETRY_AFTER_S)


def recheck_ffmpeg(config: Config | None = None) -> threading.Thread | None:
    """After failures, start ``ffmpeg -version`` in the background to learn whether
    ffmpeg starts now (z8tj1hwjvv); a later ``/info`` reports what it found. Without it
    the last failure stayed on record until a file came, and a program that waits for
    ``ok`` before sending one waited for ever. Never on the request itself: ``/info`` is
    how a client finds Upshot, with a 2 s timeout, and starting a program is slowest just
    when it has been failing. One at a time, at most once a minute
    (:meth:`FfmpegHealth.begin_recheck`). The thread, for tests; None when none starts."""
    token = HEALTH.begin_recheck()  # first: most /info calls stop here, at no cost
    if token is None:
        return None
    binary = ffmpeg_path(config)
    if binary is None:
        HEALTH.end_recheck(token)
        return None
    thread = threading.Thread(
        target=_recheck, args=(binary, token), name="ffmpeg-recheck", daemon=True
    )
    try:
        thread.start()
    except RuntimeError:  # no thread to be had: the next /info tries again
        HEALTH.end_recheck(token)
        return None
    return thread


def _recheck(binary: str, token: int) -> None:
    """Run ``ffmpeg -version`` once and record what it says, the way a file's run is
    recorded: one that started ends the failures, one that did not start for now (no
    memory) is a failure, and one that can never run is only logged, so the next file
    gets FfmpegBroken ("reinstall") rather than a hold that would last for ever. Every
    outcome ends the trial (``end_recheck``), so no upload waits on a check."""
    try:
        try:
            result = subprocess.run(
                [binary, "-hide_banner", "-version"],
                capture_output=True,
                check=False,
                timeout=FFMPEG_RECHECK_TIMEOUT_S,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            HEALTH.worked(token)  # it ran until stopped: it started, which is the question
            return
        except OSError as exc:
            if _never_runs(exc):
                _ffmpeg_broken(_os_error(exc))  # logs it; the next file says "reinstall"
                return
            why = _os_error(exc)
            log.warning("ffmpeg -version could not start: %s", why)
            HEALTH.failed(why)
            return
        if result.returncode in _BROKEN_EXITS:
            _ffmpeg_broken(_exit_text(result.returncode))
            return
        if result.returncode != 0 and _did_not_run(result.returncode, result.stderr):
            why = _exit_text(result.returncode)
            log.warning("ffmpeg -version did not run: %s", why)
            HEALTH.failed(why)
            return
        HEALTH.worked(token)  # it ran its own code, whatever it said
    except Exception:  # a thread's error would be lost; the check must still end
        log.exception("ffmpeg recheck failed")
    finally:
        HEALTH.end_recheck(token)


def _did_not_run(returncode: int, stderr: bytes) -> bool:
    """Whether ffmpeg failed before doing any work of its own. A program the loader could
    not start (0xC0000017: no memory to start it) never prints a word, while ffmpeg always
    says why it gives up. A crash after it has printed something (0xC0000005 on a crafted
    file) is the file's doing, so it stays :class:`UnsupportedAudio`, or a client told to
    try again would send the same file for ever. STATUS_NO_MEMORY is the system's either
    way: a file that asks for too much gets ffmpeg's own "Cannot allocate memory"."""
    return returncode == NO_MEMORY or (returncode != 0 and not stderr.strip())


def _exit_text(returncode: int) -> str:
    if returncode < 0:
        return f"ended by signal {-returncode}"
    return f"exit code {returncode:#010x}" if returncode > 0xFFFF else f"exit code {returncode}"


def _said(source: Path, stderr: bytes, keep: int, target: Path | None = None) -> str:
    """The end of what ffmpeg said, for the log, with the files' full paths cut down to
    their names and the home folder to ``~``: paths hold the user's account name."""
    text = stderr.decode(errors="replace")
    for path in (source, target):
        if path is not None:
            text = text.replace(str(path), path.name)
    home = str(Path.home()).rstrip("/\\")
    if home:  # not a root folder, where every separator would match
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        text = re.sub(re.escape(home) + r"(?=[\\/]|$)", "~", text, flags=flags, count=0)
    return text.strip()[-keep:]


def _os_error(exc: OSError) -> str:
    """An OSError without its file name, which is a path on this machine."""
    code = getattr(exc, "winerror", None) or exc.errno
    return f"{exc.strerror or type(exc).__name__} ({code})" if code else type(exc).__name__


#: Can never run, so waiting will not fix it (Windows error codes, ``winerror``):
#: not found (2, 3), access denied (5), not a program (193), the wrong architecture
#: (216), flagged by the antivirus (225, 226), needs elevation (740), blocked by policy
#: (1260). A sharing violation (32) and lack of memory (8, 14, 1450, and 1455: the commit
#: limit reached, the case that started this) pass, so they wait.
_NOT_A_PROGRAM_WINDOWS = {2, 3, 5, 193, 216, 225, 226, 740, 1260}
#: ... and the same on POSIX (``errno``). The two are never mixed: Windows error 8 is
#: "not enough memory", which is exactly the case that must wait, and errno 8 ENOEXEC.
_NOT_A_PROGRAM_POSIX = {errno.ENOENT, errno.ENOEXEC, errno.EISDIR, errno.EACCES, errno.EPERM}
#: How Windows ends a program it could never run: STATUS_DLL_NOT_FOUND,
#: STATUS_INVALID_IMAGE_FORMAT, STATUS_ENTRYPOINT_NOT_FOUND.
_BROKEN_EXITS = {0xC0000135, 0xC000007B, 0xC0000139}


def _ffmpeg_broken(why: str) -> FfmpegBroken:
    log.warning("ffmpeg cannot run on this computer (%s); check ffmpeg_path", why)
    return FfmpegBroken(
        f"ffmpeg cannot run on this computer ({why}): reinstall Upshot or check ffmpeg_path"
    )


def _start_failed(source: Path, exc: OSError) -> Exception:
    """What to raise when ffmpeg could not be started: FfmpegBroken when the program can
    never run, FfmpegFailed for what passes (no memory to start it, a file held)."""
    if _never_runs(exc):
        return _ffmpeg_broken(_os_error(exc))
    return _ffmpeg_failed(source, _os_error(exc))


def _never_runs(exc: OSError) -> bool:
    """Whether a start error means the program can never run, so waiting will not help."""
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in _NOT_A_PROGRAM_WINDOWS
    return exc.errno in _NOT_A_PROGRAM_POSIX


def _not_run(source: Path, returncode: int, stderr: bytes) -> Exception | None:
    """FfmpegBroken or FfmpegFailed when ffmpeg did not do its own work, else None."""
    if returncode in _BROKEN_EXITS:
        return _ffmpeg_broken(_exit_text(returncode))
    if _did_not_run(returncode, stderr):
        return _ffmpeg_failed(source, _exit_text(returncode))
    return None


def _ffmpeg_failed(source: Path, why: str) -> FfmpegFailed:
    log.warning("ffmpeg did not run on %s: %s", source.name, why)
    HEALTH.failed(why)
    return FfmpegFailed(f"ffmpeg could not run to read {source.name} ({why})")


def _missing(message: str) -> Exception:
    """No ffmpeg at all. The installed app ships one, so there it was removed (an
    antivirus, a broken update): FfmpegBroken, not the file's fault. A source checkout
    without ffmpeg can still read WAV, and says so."""
    if getattr(sys, "frozen", False):
        return _ffmpeg_broken("ffmpeg.exe is missing")
    return UnsupportedAudio(message)


@dataclass(frozen=True)
class Imported:
    records: list[ChunkRecord]
    duration_s: int
    converted: bool


#: A conversion of even a day-long recording finishes in minutes; past this, ffmpeg hangs.
FFMPEG_TIMEOUT_S = 1800


def ffmpeg_path(config: Config | None = None) -> str | None:
    configured = config.get("ffmpeg_path") if config else None
    if configured and Path(str(configured)).is_file():  # a folder is not ffmpeg
        return str(configured)
    from app import paths

    bundled = paths.resource("ffmpeg.exe")
    if bundled.exists():
        return str(bundled)
    return shutil.which("ffmpeg")


#: How often a cancellable conversion asks whether to stop.
POLL_S = 1.0

#: The containers ffmpeg may open: ordinary audio and video files. Any container that
#: names further files to read (an HLS or DASH playlist, a concat list, an image pattern)
#: is left out, because it would reach a file anywhere, a network share included, which
#: the path rules refuse (D86; the PR #1 review). ``mov`` covers mp4, m4a and 3gp, and
#: ``matroska`` covers webm.
FORMATS: tuple[str, ...] = (
    "wav", "w64", "mp3", "aac", "flac", "ogg", "matroska", "mov", "avi", "asf", "amr",
    "aiff", "au", "caf", "mpeg", "mpegts", "flv", "wv", "ape", "tta", "ac3", "eac3",
    "dts", "truehd", "mlp", "gsm", "voc", "xwma", "rm",
)  # fmt: skip
#: Before every ``-i``: local files only (R1), in one of :data:`FORMATS`.
SAFE_INPUT_ARGS: tuple[str, ...] = (
    "-protocol_whitelist", "file", "-format_whitelist", ",".join(FORMATS),
)  # fmt: skip


def to_wav(
    source: Path,
    target: Path,
    *,
    config: Config | None = None,
    rate: int = 16000,
    input_args: Sequence[str] = (),
    extra_args: Sequence[str] = (),
    stop_check: Callable[[], None] | None = None,
) -> Path:
    """Convert anything ffmpeg understands into 16 kHz mono WAV.

    ``input_args`` go before ``-i`` (``-protocol_whitelist file``), ``extra_args`` after it
    (``-vn``, so a video track is never decoded). With ``stop_check``, ffmpeg runs in the
    background and ``stop_check`` is called about once a second; whatever it raises kills
    ffmpeg, removes the partial WAV and propagates (a cancelled file transcription, D86).
    """
    binary = ffmpeg_path(config)
    if binary is None:
        raise _missing(
            f"{source.name} is not a WAV and ffmpeg was not found — convert it first, "
            "or set ffmpeg_path"
        )
    token = HEALTH.check(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        binary, "-y", *SAFE_INPUT_ARGS, *input_args, "-i", str(source), *extra_args,
        "-ac", "1", "-ar", str(rate), str(target),
    ]  # fmt: skip
    if stop_check is not None:
        return _to_wav_cancellable(command, source, target, stop_check, token)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_S,
            # The frozen app has no console, so each child would flash one of its own.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("ffmpeg took more than %s s to convert %s", FFMPEG_TIMEOUT_S, source.name)
        raise UnsupportedAudio(
            f"ffmpeg took more than {FFMPEG_TIMEOUT_S} s to convert {source.name}"
        ) from exc
    except OSError as exc:
        raise _start_failed(source, exc) from exc
    if result.returncode != 0 or not target.exists():
        raise _conversion_failed(source, result.returncode, result.stderr, target, token)
    HEALTH.worked(token)
    return target


def _conversion_failed(
    source: Path, returncode: int, stderr: bytes, target: Path | None, token: int
) -> Exception:
    failed = _not_run(source, returncode, stderr)
    if failed is not None:
        return failed
    HEALTH.worked(token)  # ffmpeg ran: it is the file it refused
    text = stderr.decode(errors="replace")
    log.warning(
        "ffmpeg could not convert %s (%s): %s",
        source.name,
        _exit_text(returncode),
        _said(source, stderr, 300, target),
    )
    if "does not contain any stream" in text or "matches no streams" in text:
        return UnsupportedAudio(f"{source.name} has no audio")
    return UnsupportedAudio(
        f"ffmpeg could not convert {source.name}: {_said(source, stderr, 300, target)}"
    )


def _to_wav_cancellable(
    command: list[str],
    source: Path,
    target: Path,
    stop_check: Callable[[], None],
    token: int,
) -> Path:
    """``to_wav`` with ffmpeg in the background, polled so that it can be stopped."""
    # stderr to a file, not a pipe: ffmpeg writes progress there for as long as it runs,
    # and an unread pipe fills and stalls it.
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise _start_failed(source, exc) from exc
        deadline = time.monotonic() + FFMPEG_TIMEOUT_S
        try:
            while True:
                try:
                    returncode = process.wait(timeout=POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    stop_check()
                    if time.monotonic() > deadline:
                        log.warning(
                            "ffmpeg took more than %s s to convert %s",
                            FFMPEG_TIMEOUT_S,
                            source.name,
                        )
                        raise UnsupportedAudio(
                            f"ffmpeg took more than {FFMPEG_TIMEOUT_S} s to convert {source.name}"
                        ) from None
        except BaseException:
            process.kill()
            process.wait()
            target.unlink(missing_ok=True)
            raise
        errors.seek(0)
        stderr = errors.read()
    if returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        raise _conversion_failed(source, returncode, stderr, target, token)
    HEALTH.worked(token)
    return target


def wav_duration_s(path: Path) -> float:
    """From the header alone: a long file's samples are never loaded to learn its length."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def read_mono(path: Path, rate: int = 16000) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        source_rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise UnsupportedAudio(f"{path.name} is {width * 8}-bit; only 16-bit WAV is supported")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if channels > 1:
        audio = audio[: len(audio) // channels * channels].reshape(-1, channels).mean(axis=1)
    if source_rate != rate:
        import soxr

        audio = np.asarray(soxr.resample(audio / 32768.0, source_rate, rate)) * 32768.0
    return np.clip(audio, -32768, 32767).astype(np.int16)


def ingest(
    source: Path,
    folder: Path,
    *,
    config: Config | None = None,
    track: str = "them",
) -> Imported:
    """Turn an uploaded file into chunk files plus a manifest under ``folder``."""
    rate = config.sample_rate if config else 16000
    converted = False
    wav = source
    if source.suffix.lower() not in WAV_SUFFIXES:
        wav = to_wav(
            source, folder / "import" / f"{source.stem}.converted.wav", config=config, rate=rate
        )
        converted = True
    try:
        samples = read_mono(wav, rate)
    except wave.Error as exc:
        if converted:
            raise UnsupportedAudio(f"{source.name}: {exc}") from exc
        wav = to_wav(
            source, folder / "import" / f"{source.stem}.converted.wav", config=config, rate=rate
        )
        converted = True
        samples = read_mono(wav, rate)

    writer = ChunkWriter(
        folder,
        tracks=(track,),
        rate=rate,
        chunk_s=float(config.chunk_s) if config else 60.0,
    )
    writer.write_pcm(track, samples)
    records = writer.close()
    duration_s = round(len(samples) / rate)
    log.info("imported %s → %d chunk(s), %d s", source.name, len(records), duration_s)
    return Imported(records=records, duration_s=duration_s, converted=converted)


#: ``Duration: 01:02:03.45`` in ffmpeg's description of its input.
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
#: How long describing a file may take; a local file is described in well under a second.
PROBE_TIMEOUT_S = 60


@dataclass(frozen=True)
class Probe:
    """What ffmpeg says a file holds, before anything is decoded (D86)."""

    duration_s: float | None
    has_audio: bool


def probe(source: Path, *, config: Config | None = None) -> Probe:
    """Describe ``source`` with ``ffmpeg -i`` and no output: the build ships no ffprobe.

    Local files in ordinary containers only (:data:`SAFE_INPUT_ARGS`), as every
    conversion reads them. Raises
    :class:`UnsupportedAudio` for anything ffmpeg cannot open, and :class:`FfmpegFailed`
    when ffmpeg itself did not run; a file it opens with no audio stream comes back with
    ``has_audio=False``.
    """
    binary = ffmpeg_path(config)
    if binary is None:
        raise _missing("ffmpeg was not found, so this file cannot be read")
    token = HEALTH.check(source)
    try:
        result = subprocess.run(
            # No -hide_banner: ffmpeg that started has always printed its banner, so an
            # empty stderr means it never ran (_did_not_run), even if the file crashed it.
            [binary, *SAFE_INPUT_ARGS, "-i", str(source)],
            capture_output=True,
            check=False,
            timeout=PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("ffmpeg took more than %s s to read %s", PROBE_TIMEOUT_S, source.name)
        raise UnsupportedAudio(f"ffmpeg took too long to read {source.name}") from exc
    except OSError as exc:
        raise _start_failed(source, exc) from exc
    failed = _not_run(source, result.returncode, result.stderr)
    if failed is not None:
        raise failed
    HEALTH.worked(token)  # ffmpeg ran, whatever it makes of the file
    text = result.stderr.decode(errors="replace")
    if "Input #0" not in text:
        log.info("ffmpeg cannot read %s: %s", source.name, _said(source, result.stderr, 200))
        raise UnsupportedAudio(f"{source.name} is not audio or video ffmpeg can read")
    match = _DURATION.search(text)
    duration = None
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    has_audio = re.search(r"Stream #\S+.*: Audio:", text) is not None
    return Probe(duration_s=duration, has_audio=has_audio)
