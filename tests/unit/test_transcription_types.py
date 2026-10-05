"""The file transcription's shared types (D86)."""

from __future__ import annotations

import pytest

from app.transcription.types import PHASE_WEIGHTS, PHASES, Options, overall_progress


def test_the_phase_weights_cover_the_whole_job() -> None:
    assert set(PHASE_WEIGHTS) == set(PHASES)
    assert sum(PHASE_WEIGHTS.values()) == pytest.approx(1.0)


def test_overall_progress_is_weighted_by_phase() -> None:
    assert overall_progress("decode", 0.0) == 0.0
    assert overall_progress("decode", 1.0) == pytest.approx(0.05)
    assert overall_progress("transcribe", 0.5) == pytest.approx(0.1 + 0.375)
    assert overall_progress("diarize", 1.0) == pytest.approx(1.0)
    assert overall_progress("transcribe", 7.0) == pytest.approx(0.85), "a fraction is clamped"


def test_an_unknown_phase_is_a_bug() -> None:
    with pytest.raises(ValueError, match="unknown phase"):
        overall_progress("summarize", 0.5)


def test_options_round_trip_with_defaults() -> None:
    options = Options(language="he", diarize=False, prompt="דיברה", max_words_per_cue=5)
    assert Options.from_dict(options.as_dict()) == options
    assert Options.from_dict({}) == Options()
