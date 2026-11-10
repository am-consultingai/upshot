"""transcript.md → notes.json: window, map, reduce, then the sanity gates (§9)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import glossary as glossary_module
from app import meta
from app.asr.languages import english_name
from app.asr.models import DEFAULT_LANGUAGE
from app.due import anchor_date, resolve_due
from app.errors import Deferred, QuotaExhausted, SignInRequired
from app.llm import schema
from app.llm.client import LlmClient, LlmResult, make_client, system_blocks
from app.llm.prompts import load as load_prompt
from app.llm.schema import FREE_SCHEMA
from app.llm.tokens import CachingCounter, TokenCounter
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext
from app.pipeline.stages.assemble import transcript_paths

log = get(__name__)

NOTES_NAME = "notes.json"
LONG_MEETING_S = 20 * 60

#: How much transcript one call carries when ``llm.window_tokens`` is left null. 6000 was
#: once the only value, and for a hosted model it is a poor one: a 25 kB Hebrew transcript
#: became two windows plus a merge — three calls of 1.5 to 2 minutes each through Claude
#: Code, where one call of the same length would have done. The merge also costs quality
#: under the free-form prompt, which is asked to fuse two finished HTML documents.
WINDOW_TOKENS_BY_PROVIDER: dict[str, int] = {
    "anthropic": 150_000,
    "claude-subscription": 150_000,
    # Estimated at 2 chars/token (no token endpoint behind a CLI), so this is ~300k
    # characters: comfortably inside the Codex models' context.
    "codex-subscription": 150_000,
    # The same estimate; Antigravity's Gemini models take far more than this.
    "antigravity-subscription": 150_000,
    "gemini": 150_000,
    "openai": 100_000,
}
#: Local models and anything unrecognised: a small context is the safe assumption.
DEFAULT_WINDOW_TOKENS = 6000


def notes_path(folder: Path) -> Path:
    return Path(folder) / NOTES_NAME


@dataclass(frozen=True)
class Window:
    index: int
    start: int
    end: int
    text: str


def split_windows(
    text: str,
    counter: TokenCounter,
    *,
    target_tokens: int = 6000,
    overlap_tokens: int = 300,
) -> list[Window]:
    """Window by **real tokens**, found by binary search over character boundaries."""
    if not text:
        return []
    if counter.count(text) <= target_tokens:
        return [Window(0, 0, len(text), text)]
    windows: list[Window] = []
    start = 0
    while start < len(text):
        end = _boundary(text, start, counter, target_tokens)
        windows.append(Window(len(windows), start, end, text[start:end]))
        if end >= len(text):
            break
        overlap_chars = _overlap_chars(text, end, counter, overlap_tokens)
        next_start = end - overlap_chars
        start = next_start if next_start > start else end
    return windows


def _boundary(text: str, start: int, counter: TokenCounter, target: int) -> int:
    low, high = start + 1, len(text)
    best = start + 1
    while low <= high:
        mid = (low + high) // 2
        if counter.count(text[start:mid]) <= target:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return min(best, len(text))


def _overlap_chars(text: str, end: int, counter: TokenCounter, overlap_tokens: int) -> int:
    if overlap_tokens <= 0:
        return 0
    low, high = 0, end
    best = 0
    while low <= high:
        mid = (low + high) // 2
        if counter.count(text[end - mid : end]) <= overlap_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def window_tokens(ctx: StageContext, client: LlmClient) -> int:
    """An explicit ``llm.window_tokens`` wins; null sizes the window to the provider."""
    configured = ctx.config.get("llm.window_tokens")
    if configured is not None:
        return int(configured)
    return WINDOW_TOKENS_BY_PROVIDER.get(client.name, DEFAULT_WINDOW_TOKENS)


def glossary_block(ctx: StageContext) -> str | None:
    entries = glossary_module.active(ctx.dao, ctx.config.glossary_path)
    if not entries:
        return None
    lines = ["Glossary — keep these spellings verbatim:"]
    lines.extend(f"- {entry.as_prompt_fragment()}" for entry in entries)
    return "\n".join(lines)


def resolve_summary_language(ctx: StageContext) -> str:
    configured = ctx.config.summary_language
    if configured != "auto":
        return configured
    return ctx.meeting.language or DEFAULT_LANGUAGE


def language_instruction(language: str) -> str:
    """Which language the notes are written in: any Whisper language, by its name."""
    name = english_name(language)
    return (
        f"Write the notes in {name} ({language}). Keep proper nouns, product names and "
        "technical terms verbatim in their original language."
    )


def client_for(ctx: StageContext) -> LlmClient:
    services = ctx.services
    client = getattr(services, "llm", None) if services is not None else None
    if client is not None:
        return client  # type: ignore[no-any-return]
    # On the worker: it may ask the provider for its model list if the choice needs it.
    return make_client(ctx.config, sensitive=bool(ctx.meeting.sensitive), fetch=True)


#: Optional in an edited prompt: where the calendar invitation should be written. Without
#: it the invitation goes in front of the transcript instead, so a prompt that never heard
#: of the calendar still gets the context.
MEETING_SLOT = "{{meeting}}"


def system_text(ctx: StageContext) -> tuple[str, str]:
    """The summarizing instructions, and the version to record against them.

    An edited prompt replaces the shipped one rather than appending to it. Appending
    would mean the box in Settings shows something other than what is sent, and a prompt
    you cannot fully see is one you cannot debug.
    """
    shipped = load_prompt("system")
    custom = ctx.config.get("llm.summary_prompt")
    if isinstance(custom, str) and custom.strip():
        text = custom.strip()
        # Hashed, not just "custom": every edit has to be a different version, or a
        # second edit looks identical to the first and the notes are never rebuilt.
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        return text, f"custom:{digest}"
    return shipped.text, shipped.version


def meeting_context(ctx: StageContext) -> str | None:
    """What the model is told about the meeting before it reads the transcript.

    Always its date and weekday — the model is asked for ``due_at`` and "by Thursday" is
    unresolvable without knowing which week it was said in, and a recording made with no
    calendar invitation is exactly the one that would otherwise carry no date at all.
    Then, where there is one, the calendar invitation (:func:`calendar_context`).
    """
    parts = [calendar_context(ctx), details_context(ctx), date_context(ctx.meeting.started_at)]
    joined = "\n\n".join(part for part in parts if part)
    return joined or None


def details_context(ctx: StageContext) -> str | None:
    """What the user wrote about the meeting when recording it, or later: its title as they
    named it and their description. Nothing when they wrote nothing."""
    meeting = ctx.meeting
    lines = []
    if meeting.title and meeting.title_source == "user":
        lines.append(f"Meeting title (from the user): {meeting.title}")
    if meeting.description and meeting.description.strip():
        lines.append("The user's description of the meeting:\n" + meeting.description.strip())
    return "\n".join(lines) or None


def date_context(started_at: str) -> str | None:
    """ "Date: 2026-09-23 (Wednesday), 14:00 local time." — the anchor for deadlines."""
    from datetime import datetime

    try:
        when = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    local = when.astimezone() if when.tzinfo else when
    return (
        f"Date: {local.date().isoformat()} ({local:%A}), {local:%H:%M} local time. "
        'Spoken deadlines such as "by Thursday" count from this day.'
    )


def calendar_context(ctx: StageContext) -> str | None:
    """What the calendar knows about this meeting, for the model (Calendar 4).

    The invitation is read live and in full — title, times, organizer, who was invited,
    the agenda the organizer wrote, the links in it and the files attached to it — because
    a model that knows what the meeting was *for* writes a better summary of what was
    said. Email addresses are not part of it: an attendee is a name by the time this sees
    them.

    Advisory, like every other calendar path: no network, no connection or no match means
    the snapshot taken at match time is used instead, and failing that, nothing.
    """
    from app.meetings import calendar_payload, event_account

    if not ctx.config.get("calendar.prompt_invite", True):
        return None
    payload = calendar_payload(ctx.meeting)
    if (payload.get("match") or {}).get("state") != "matched":
        return None
    reader = getattr(ctx.services, "calendar_invites", None)
    ref = payload.get("event") or {}
    account = event_account(ctx.meeting)
    if reader is not None and ref and account:
        try:
            invite = reader.fetch(account, str(ref["calendar_id"]), str(ref["event_id"]))
            return str(invite.as_prompt())
        except Exception as exc:
            # The meeting is summarized whatever the calendar is doing.
            log.info("invitation unavailable; summarizing without it (%s)", exc)
    return snapshot_context(payload)


def snapshot_context(payload: dict[str, Any]) -> str | None:
    """The fallback: what was stored when the recording was matched."""
    lines: list[str] = []
    if payload.get("title"):
        lines.append(f"Title: {payload['title']}")
    names = [str(name) for name in payload.get("participants") or [] if name]
    if names:
        more = int(payload.get("participants_more") or 0)
        listed = ", ".join(names) + (f", and {more} more" if more else "")
        lines.append(
            f"Invited: {listed}. Action items should belong to one of these people or to "
            "the speaker labelled ME."
        )
    if not lines:
        return None
    return "Meeting details, from the calendar invitation:\n" + "\n".join(lines)


def summarize(
    ctx: StageContext,
    transcript: str,
    *,
    system: str,
    system_version: str,
    client: LlmClient | None = None,
    instead_of: Deferred | None = None,
    chosen_for: str | None = None,
) -> None:
    """Free-form: the prompt writes the summary, and nothing here rewrites it.

    Structured mode exists because the rest of the application reads the parts — the
    title indexes the meeting, action-item owners drive the review gates, the template
    lays the sections out. All of that is a constraint the prompt cannot see past, so a
    prompt asking for a different shape of document could not have any visible effect.

    Here the only thing still imposed is the JSON envelope, and only because it is what
    makes an answer extractable at all across providers. What is inside ``summary_html``
    is entirely the prompt's: sections, order, wording, layout.
    """
    client = client or client_for(ctx)
    counter = CachingCounter(client.count_tokens)
    language = resolve_summary_language(ctx)
    glossary = glossary_block(ctx)
    windows = split_windows(
        transcript,
        counter,
        target_tokens=window_tokens(ctx, client),
        overlap_tokens=int(ctx.config.get("llm.window_overlap_tokens", 300)),
    )
    log.info("summarizing %d window(s) in %s (free-form)", len(windows), language)

    envelope = (
        "Reply with a JSON object holding `summary_html` — the complete summary as HTML — "
        "and optionally `title`, `action_items` and `chapters`. Everything about that "
        "HTML is yours to decide. `action_items` is an array of "
        '{"who", "what", "due"?, "at_ms"?, "detail"?, "due_at"?} and it is where the '
        "commitments belong: the application renders it as a checkable list directly "
        "above your document, so a second list of the same commitments inside the HTML "
        "is shown twice to the reader. Write them here, and let the document say what "
        "was decided and why rather than repeating who owes what. `detail` is one short "
        "line: why it matters, what it unblocks, or who is waiting on it. `due_at` is "
        "the `due` resolved to a calendar date (YYYY-MM-DD) using the meeting date given "
        "in the details; null when no date was said. `chapters` is an array of 3–7 "
        '{"title", "start_ms", "end_ms"} topic sections covering the conversation in '
        "order, with start and end in milliseconds from the transcript's timestamps."
    )
    instruction = language_instruction(language)
    context = meeting_context(ctx)
    # An edited prompt can place the invitation itself. Where it does, the block goes
    # there and not in front of the transcript, so the prompt in Settings is the prompt
    # that is sent.
    if MEETING_SLOT in system:
        system = system.replace(MEETING_SLOT, context or "")
        context = None
    parts: list[str] = []
    usage: list[dict[str, Any]] = []
    model_title = ""
    # Collected per window and merged, because a two-hour meeting is summarized in
    # pieces and a commitment made in the first hour belongs in the list as much as
    # one made in the last.
    items: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    for window in windows:
        ctx.checkpoint()
        result = client.complete_json(
            system_blocks=system_blocks(f"{system}\n\n{instruction}\n\n{envelope}", glossary),
            # The invitation leads, then the transcript: the model reads what the meeting
            # was for before it reads what was said.
            user=f"{context}\n\nTranscript:\n{window.text}" if context else window.text,
            schema=FREE_SCHEMA,
            max_tokens=int(ctx.config.get("llm.max_tokens", 16000)),
        )
        usage.append(result.usage)
        parts.append(str(result.data.get("summary_html", "")))
        model_title = model_title or str(result.data.get("title", "") or "").strip()
        items.extend(schema.action_items(result.data))
        chapters.extend(schema.chapters(result.data))
        log.info("window %d/%d done", window.index + 1, len(windows))

    if len(parts) > 1:
        ctx.checkpoint()
        merged = client.complete_json(
            system_blocks=system_blocks(f"{system}\n\n{instruction}\n\n{envelope}", glossary),
            user="Merge these partial summaries of one meeting into a single document:\n\n"
            + "\n\n---\n\n".join(parts),
            schema=FREE_SCHEMA,
            max_tokens=int(ctx.config.get("llm.max_tokens", 16000)),
        )
        usage.append(merged.usage)
        model_title = str(merged.data.get("title", "") or "").strip() or model_title
        notes: dict[str, Any] = {"summary_html": str(merged.data.get("summary_html", ""))}
        # The merge sees every partial summary, so its list is the deduplicated one.
        # It is only trusted where it produced something: a model that merged the
        # documents and dropped the list should not cost the user their action items.
        items = schema.action_items(merged.data) or items
        # The same rule for chapters: the merge's own, else every window's in time order.
        chapters = schema.chapters(merged.data) or schema.chapters({"chapters": chapters})
    else:
        notes = {"summary_html": parts[0]}
    # Title precedence (Calendar 4): the user, then the calendar, then the model, then
    # the window title. The model's suggestion only replaces a window guess or nothing.
    meeting = ctx.meeting
    if model_title and (not meeting.title or meeting.title_source in ("window", "default")):
        ctx.dao.update_meeting(meeting.id, title=model_title[:200], title_source="llm")
        meeting = ctx.refresh()
    # The model's own suggestion, as it wrote it: content, like the rest of the notes. The
    # meeting's name is the database's (D89), and is not copied here.
    if model_title:
        notes["title"] = model_title[:200]
    # A model that said "by Thursday" and left `due_at` out gets it resolved here, against
    # the day the meeting happened, so the stored row carries a date either way.
    anchor = anchor_date(meeting.started_at)
    for item in items:
        if not item.get("due_at") and item.get("due") and anchor is not None:
            resolved = resolve_due(item["due"], anchor)
            item["due_at"] = resolved.isoformat() if resolved else None
    notes["action_items"] = items
    notes["chapters"] = chapters

    notes_path(ctx.folder).write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Into the database as well as notes.json: the file is this meeting's record, the
    # table is what lets the inbox read across all of them. Ticks already made survive
    # this — see Dao.replace_action_items.
    stored = ctx.dao.replace_action_items(ctx.meeting.id, items)
    log.info("stored %d action item(s)", stored)
    ctx.dao.update_meeting(ctx.meeting.id, summary_language=language)
    # No sanity gates: they read action_items and owners, which free-form has no notion
    # of. Nothing here is in a position to judge what the prompt asked for.
    meta.mirror(
        ctx.refresh(),
        prompt_versions={"system": system_version, "format": "free"},
        summary_language=language,
        windows=len(windows),
        llm=written_by(client, _sum_usage(usage), instead_of, chosen_for=chosen_for),
    )
    ctx.metrics["written_by"] = client.name  # it ran and has allowance (the worker clears a mark)
    log.info("wrote free-form notes (%d chars)", len(notes["summary_html"]))


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    _, transcript_md = transcript_paths(folder)
    output = notes_path(folder)
    system, system_version = system_text(ctx)
    # The prompt is an input to the notes, so it belongs in the staleness test. Comparing
    # against the transcript alone meant editing the prompt and pressing Summarize again
    # did nothing at all: the transcript had not moved, so the stage skipped and the old
    # summary stood.
    recorded = str(meta.read(folder).get("prompt_versions", {}).get("system", ""))
    # Read here, before anything can end the run early, and kept until the run ends: a
    # recording that preempts it, or a crash, must not lose the user's choice.
    chosen = chosen_provider(ctx)
    if ctx.force or chosen is not None:
        # Asked for by hand: no staleness test, no comparison against what is on disk.
        # Pressing Summarize is a decision, and second-guessing it is how an edited
        # prompt came to have no visible effect at all.
        log.info("re-summarizing on request")
    elif up_to_date(output, [transcript_md]) and recorded == system_version:
        log.info("notes.json is current; skipping")
        return
    elif recorded and recorded != system_version:
        log.info("prompt changed (%s -> %s); re-summarizing", recorded, system_version)
    if not transcript_md.exists():
        raise FileNotFoundError(f"{transcript_md} is missing — run the assemble stage first")

    transcript = transcript_md.read_text(encoding="utf-8")
    if chosen is not None:
        # Picked on the meeting page while the usual provider could not run: this run,
        # this provider, and the notes say so. Cleared when the run ends (the notes are
        # written, or the job fails for good: the worker), so a later automatic run (a
        # calendar change, a merge) uses Settings again. If the picked provider is signed
        # out too, the job waits on it — the provider the page then names. If its allowance
        # turns out spent, the pick is given up and this run goes on as if never made: the
        # usual provider (or its fallback), else a wait the page can offer a way out of.
        configured = str(ctx.config.get("llm.provider", "") or "")
        log.info("summarizing with %s, chosen for this meeting", chosen)
        try:
            summarize(
                ctx,
                transcript,
                system=system,
                system_version=system_version,
                client=make_client(
                    ctx.config, provider=chosen, sensitive=bool(ctx.meeting.sensitive), fetch=True
                ),
                chosen_for=configured if configured not in ("", "none", chosen) else None,
            )
        except QuotaExhausted as spent:
            log.warning("%s, chosen for this meeting, is out of allowance: %s", chosen, spent)
            note_spent(ctx, spent)
            drop_chosen_provider(folder, only=chosen)
        else:
            drop_chosen_provider(folder, only=chosen)
            return
    try:
        summarize(ctx, transcript, system=system, system_version=system_version)
    except (QuotaExhausted, SignInRequired) as stuck:
        note_spent(ctx, stuck)
        fallback = fallback_client(ctx, stuck)
        if fallback is None:
            raise  # the worker parks the job until the allowance resets or a sign-in
        log.warning(
            "%s cannot run (%s); summarizing with the configured fallback %s instead",
            stuck.provider,
            type(stuck).__name__,
            fallback.name,
        )
        try:
            summarize(
                ctx,
                transcript,
                system=system,
                system_version=system_version,
                client=fallback,
                instead_of=stuck,
            )
        except Deferred as also:
            log.warning("the fallback %s cannot run either: %s", fallback.name, also)
            note_spent(ctx, stuck, also)
            if isinstance(stuck, QuotaExhausted) and isinstance(also, QuotaExhausted):
                # Both allowances spent: wait for whichever comes back first, which then
                # writes the summary (the usual provider, or the fallback after it).
                raise sooner(stuck, also, ctx.clock.now()) from None
            if isinstance(stuck, QuotaExhausted) and isinstance(also, SignInRequired):
                # The usual provider is out until its reset, perhaps days away; signing in
                # the fallback is what the user can do now, so that is what the job waits
                # for (and the page and Settings offer).
                raise also from stuck
            if isinstance(also, SignInRequired):
                # Waiting on the usual provider, whose message the user can act on; the
                # fallback's held status must not keep offering it on the meeting page.
                status = getattr(ctx.services, "cli_status", None) if ctx.services else None
                if status is not None and also.provider:
                    status.forget(also.provider)
            raise stuck from also


def back_at(deferred: Deferred, now: datetime) -> datetime:
    """When a provider that said "not now" can run again, as the worker reads it: an
    unstated or past time counts as the class's default wait."""
    at = deferred.retry_at
    if at is not None and at.tzinfo is None:
        at = at.astimezone()
    default = now + timedelta(seconds=deferred.default_wait_s)
    return default if at is None or at <= now else at


def sooner(first: Deferred, second: Deferred, now: datetime) -> Deferred:
    """Of two providers that are both out, the one back first."""
    return second if back_at(second, now) < back_at(first, now) else first


def note_spent(ctx: StageContext, *errors: BaseException) -> None:
    """Remember a provider whose allowance is used up, so the meeting page does not offer
    it until it is back (the CLI's status cannot tell)."""
    status = getattr(ctx.services, "cli_status", None) if ctx.services is not None else None
    if status is None:
        return
    now = ctx.clock.now()
    for error in errors:
        if isinstance(error, QuotaExhausted) and error.provider:
            status.spent(error.provider, back_at(error, now))


def chosen_provider(ctx: StageContext) -> str | None:
    """The provider picked on the meeting page for its next summary, or ``None``.

    Never for a sensitive meeting: that one is summarized on this computer, whatever
    anyone picked (the endpoint refuses it too), and a pick found on one is dropped.
    """
    picked = meta.read(ctx.folder).get("summary_provider")
    if picked is None:
        return None
    if ctx.meeting.sensitive or not isinstance(picked, str) or not picked:
        meta.update(ctx.folder, summary_provider=None)
        return None
    return picked


def drop_chosen_provider(folder: Path, *, only: str | None = None) -> None:
    """The run the pick was for has ended: the next one uses Settings. With ``only``, a
    pick made meanwhile — a different provider, for the run after this one — is kept."""
    picked = meta.read(folder).get("summary_provider")
    if picked is not None and (only is None or picked == only):
        meta.update(folder, summary_provider=None)


def fallback_client(ctx: StageContext, spent: Deferred) -> LlmClient | None:
    """The provider the user chose for exactly this case, or ``None`` — never a guess.

    Only a spent allowance or a signed-out CLI reaches here. A fallback that is unset,
    that names the provider which just stopped, or a meeting marked sensitive (which
    never left the machine to begin with) means the summary waits instead: a transcript
    going to a provider nobody picked is the surprise this application exists to avoid.
    """
    injected = getattr(ctx.services, "fallback_llm", None) if ctx.services is not None else None
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    chosen = str(ctx.config.get("llm.fallback_provider", "") or "")
    if not chosen or chosen == spent.provider or ctx.meeting.sensitive:
        return None
    return make_client(ctx.config, provider=chosen, fetch=True)


def written_by(
    client: LlmClient,
    usage: dict[str, int],
    instead_of: Deferred | None,
    *,
    chosen_for: str | None = None,
) -> dict[str, Any]:
    """Which provider wrote these notes, for ``meta.json`` and the meeting page.

    When it was the fallback, that is recorded with the reason, so a summary that came
    from somewhere other than the chosen provider is never mistaken for one that did not.
    """
    record: dict[str, Any] = {"name": client.name, "usage": usage}
    if instead_of is not None:
        record["fallback_for"] = instead_of.provider
        record["fallback_reason"] = str(instead_of)
    elif chosen_for:
        record["fallback_for"] = chosen_for
        record["fallback_reason"] = "Chosen for this meeting on its page."
    return record


def _sum_usage(usage: Sequence[dict[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for entry in usage:
        for key, value in entry.items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


def load_notes(folder: Path) -> dict[str, Any]:
    payload = json.loads(notes_path(folder).read_text(encoding="utf-8"))
    return dict(payload)


def result_is_valid(result: LlmResult) -> bool:
    from app.llm.schema import is_valid

    return is_valid(result.data)
