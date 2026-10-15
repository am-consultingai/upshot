import importlib.machinery
import sys
import types


def _no_pyav() -> types.ModuleType:
    """A stand-in for PyAV, which Upshot does not ship.

    faster-whisper imports PyAV when it is imported (``faster_whisper/audio.py``) but uses
    it only to decode a file it is given by path. PyAV's Windows wheel bundles the GPL
    encoders libx264 and libx265, so ``pyproject.toml`` keeps it out of every environment
    and audio reaches faster-whisper as arrays (``app/asr/wav.py``). Anything that still
    reaches for PyAV fails here, loudly.
    """
    module = types.ModuleType("av")
    # A spec, so ``importlib.util.find_spec("av")`` answers instead of raising ValueError.
    module.__spec__ = importlib.machinery.ModuleSpec("av", None, origin="Upshot stand-in")

    def __getattr__(name: str) -> object:
        if name.startswith("__"):  # introspection (``hasattr(av, "__path__")``) stays quiet
            raise AttributeError(name)
        raise ImportError(f"PyAV is not part of Upshot (av.{name}); pass audio as an array")

    module.__getattr__ = __getattr__  # type: ignore[method-assign]
    return module


sys.modules.setdefault("av", _no_pyav())
