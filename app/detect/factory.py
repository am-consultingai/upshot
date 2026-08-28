"""Which signal sources are wired — real Windows APIs, or the fakes."""

from __future__ import annotations

from app.audio.vad import TwoStageVad
from app.config import Config
from app.detect.sources import Sources, fake_sources
from app.log import get

log = get(__name__)


class RecorderVad:
    """Sustained speech per track, read from the recorder's own pre-roll ring.

    "Sustained" is the design's definition: at least 3 s of voiced frames inside a
    rolling 10 s window (DETECTION.md §5).
    """

    def __init__(
        self,
        recorder: object,
        *,
        window_s: float = 10.0,
        min_voiced_s: float = 3.0,
        vad: TwoStageVad | None = None,
    ) -> None:
        self.recorder = recorder
        self.window_s = window_s
        self.min_voiced_s = min_voiced_s
        self.vad = vad or TwoStageVad()

    def sustained(self, track: str) -> bool:
        runtime = getattr(self.recorder, "runtime", {}).get(track)
        if runtime is None:
            return False
        samples = runtime.ring.peek()
        rate = getattr(self.recorder, "rate", 16000)
        window = int(self.window_s * rate)
        if len(samples) == 0:
            return False
        return bool(self.vad.has_speech(samples[-window:], rate, min_s=self.min_voiced_s))


class CameraConsentSource:
    def __init__(self) -> None:
        from app.detect.registry import WEBCAM_KEY, ConsentStoreReader

        self.reader = ConsentStoreReader(key=WEBCAM_KEY)

    def in_use(self) -> bool:
        return bool(self.reader.current_holders())


def make_sources(config: Config, recorder: object | None = None) -> Sources:
    kind = str(config.get("detection.sources", "windows"))
    if kind == "fake":
        return fake_sources()
    from app.detect.registry import ConsentStoreReader
    from app.detect.sessions import PycawSessions
    from app.detect.windows import EnumWindowTitles

    vad = RecorderVad(recorder) if recorder is not None else fake_sources().vad
    return Sources(
        mic=ConsentStoreReader(),
        sessions=PycawSessions(),
        titles=EnumWindowTitles(),
        camera=CameraConsentSource(),
        vad=vad,
    )
