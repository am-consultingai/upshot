"""The stage registry: the one place that maps a job's stage name to its function."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.pipeline.context import StageContext
from app.pipeline.states import JobStage

StageFn = Callable[[StageContext], None]


def registry() -> Mapping[str, StageFn]:
    from app.pipeline.stages import assemble, deliver, render, summarize, transcribe

    return {
        str(JobStage.TRANSCRIBE): transcribe.run,
        str(JobStage.ASSEMBLE): assemble.run,
        str(JobStage.SUMMARIZE): summarize.run,
        str(JobStage.RENDER): render.run,
        str(JobStage.DELIVER): deliver.run,
    }
