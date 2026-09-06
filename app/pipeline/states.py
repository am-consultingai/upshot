"""Meeting and job states, and the legal transitions between them.

An illegal transition is a bug, not a runtime condition: the DAO raises.
"""

from __future__ import annotations

from enum import StrEnum
from itertools import pairwise


class MeetingState(StrEnum):
    ARMED = "ARMED"  # reserved for a future calendar source; unreachable in this build
    RECORDING = "RECORDING"
    RECORDED = "RECORDED"
    TRANSCRIBING = "TRANSCRIBING"  # transcribe + assemble
    TRANSCRIBED = "TRANSCRIBED"  # transcript.md exists
    SUMMARIZING = "SUMMARIZING"
    SUMMARIZED = "SUMMARIZED"
    RENDERED = "RENDERED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    DISCARDED = "DISCARDED"


class JobStage(StrEnum):
    TRANSCRIBE = "transcribe"
    ASSEMBLE = "assemble"
    SUMMARIZE = "summarize"
    RENDER = "render"
    DELIVER = "deliver"


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


M = MeetingState

#: The happy path, in order.
PIPELINE: tuple[MeetingState, ...] = (
    M.RECORDING,
    M.RECORDED,
    M.TRANSCRIBING,
    M.TRANSCRIBED,
    M.SUMMARIZING,
    M.SUMMARIZED,
    M.RENDERED,
    M.DELIVERED,
)

#: Which stage runs while a meeting is in which state, and where it lands.
STAGE_ORDER: tuple[JobStage, ...] = (
    JobStage.TRANSCRIBE,
    JobStage.ASSEMBLE,
    JobStage.SUMMARIZE,
    JobStage.RENDER,
    JobStage.DELIVER,
)

STAGE_RUNNING_STATE: dict[JobStage, MeetingState] = {
    JobStage.TRANSCRIBE: M.TRANSCRIBING,
    JobStage.ASSEMBLE: M.TRANSCRIBING,
    JobStage.SUMMARIZE: M.SUMMARIZING,
    JobStage.RENDER: M.SUMMARIZED,
    JobStage.DELIVER: M.RENDERED,
}

STAGE_DONE_STATE: dict[JobStage, MeetingState] = {
    JobStage.TRANSCRIBE: M.TRANSCRIBING,
    JobStage.ASSEMBLE: M.TRANSCRIBED,
    JobStage.SUMMARIZE: M.SUMMARIZED,
    JobStage.RENDER: M.RENDERED,
    JobStage.DELIVER: M.DELIVERED,
}

#: States a meeting can still be worked on from.
ACTIVE: frozenset[MeetingState] = frozenset(PIPELINE[:-1])

#: States from which a retry may re-enter the pipeline.
RETRYABLE_ENTRY: frozenset[MeetingState] = frozenset(
    {M.RECORDED, M.TRANSCRIBING, M.TRANSCRIBED, M.SUMMARIZING, M.SUMMARIZED, M.RENDERED}
)


def _build() -> frozenset[tuple[MeetingState, MeetingState]]:
    pairs: set[tuple[MeetingState, MeetingState]] = set()
    for a, b in pairwise(PIPELINE):
        pairs.add((a, b))
    # a stage that both starts and finishes in the same state (transcribe → assemble)
    pairs.add((M.TRANSCRIBING, M.TRANSCRIBING))
    for state in ACTIVE | {M.ARMED}:
        pairs.add((state, M.FAILED))
        pairs.add((state, M.INTERRUPTED))
        pairs.add((state, M.DISCARDED))
    # retry / re-run
    for state in RETRYABLE_ENTRY:
        pairs.add((M.FAILED, state))
        pairs.add((M.INTERRUPTED, state))
    pairs.add((M.RENDERED, M.SUMMARIZING))  # re-summarize with a new prompt
    pairs.add((M.DELIVERED, M.SUMMARIZING))
    pairs.add((M.RENDERED, M.SUMMARIZED))  # re-render
    pairs.add((M.DELIVERED, M.RENDERED))
    pairs.add((M.TRANSCRIBED, M.TRANSCRIBING))  # re-transcribe
    pairs.add((M.SUMMARIZED, M.SUMMARIZING))
    pairs.add((M.ARMED, M.RECORDING))
    pairs.add((M.INTERRUPTED, M.RECORDED))
    pairs.add((M.DISCARDED, M.RECORDED))  # "it was a meeting after all"
    return frozenset(pairs)


LEGAL_TRANSITIONS: frozenset[tuple[MeetingState, MeetingState]] = _build()


def is_legal(old: MeetingState, new: MeetingState) -> bool:
    return (old, new) in LEGAL_TRANSITIONS


def next_stage(stage: JobStage) -> JobStage | None:
    idx = STAGE_ORDER.index(stage)
    if idx + 1 < len(STAGE_ORDER):
        return STAGE_ORDER[idx + 1]
    return None
