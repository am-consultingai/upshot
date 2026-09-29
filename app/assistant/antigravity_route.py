"""The assistant on the user's Google AI plan: Upshot looks things up, Antigravity answers.

Claude Code and Codex call Upshot's tools themselves, over its MCP server. ``agy`` cannot
be given that server for one run: ``agy mcp add`` writes the user's own configuration, and
a ``.mcp.json`` in the run's folder is not read (checked 2026-09-29). And it must not have
tools at all: it applies the user's own permission rules, which can allow every shell
command (D79). So it runs as the tool-less ``upshot`` agent
(``antigravity_cli.ensure_agent``), and Upshot does the looking up first, with the same
read-only functions the tool server serves (``AssistantTools``):

* each meaningful word of the question searched (the search matches a phrase as it is,
  so a whole question would match nothing);
* the meeting on screen, when the question is about it: its summary, action items and
  transcript;
* recent meetings and open action items, which answer the "this week", "what do I owe"
  kind of question without a search word to find them by.

That is fenced as data (``as_data``) and put in the prompt with the question; the answer
streams back with the same citation markers the other routes produce, so the panel,
the citations and the stored conversation are the same. What this cannot do is look
again half-way through an answer: a question the gathered text does not cover is said to
be so, rather than answered from a second search.
"""

from __future__ import annotations

import re
from typing import Any

from app.assistant import stream
from app.assistant.citations import Citer
from app.assistant.claude_route import ClaudeRoute, Translator, Turn, problem_code
from app.assistant.tools import AssistantTools
from app.errors import PermanentError, QuotaExhausted, RecoverableError
from app.llm import antigravity_cli

NAME = "antigravity-subscription"

#: Words too common to search for, in the two languages the app speaks.
_STOPWORDS_TEXT = """
    a an and are about after all also any as at be been before but by can could did do
    does done for from had has have how i if in into is it its last me my next not now of
    on or our out over said say she should so than that the their them then there these
    they this those to up us was we were what when where which who whom why will with
    would you your yet week today yesterday tomorrow meeting meetings decide decided
    של את על עם זה זו מה מי מתי איך למה לא כן גם או אם כל יש אין היה היו הוא היא הם
    אני אנחנו אתה את שלי שלנו פגישה פגישות החלטנו סיכום
"""
STOPWORDS = frozenset(_STOPWORDS_TEXT.split())
#: How much gathered text goes with one question, in characters, at most.
GATHER_CHARS = 60_000

#: Instead of the tool instructions in the assistant prompt: there are no tools here.
NO_TOOLS = (
    "\n\nIn this conversation you have no tools. Upshot has already looked up what may be "
    "relevant to the question, and it is below, between the data tags. Answer only from "
    "it. Cite with the meeting ids and at_ms values it contains, exactly as the rules above "
    "say. If it does not contain the answer, say so plainly, say what was looked for, and "
    "suggest how the user could ask to find it (a word that would be in the meeting, or "
    "opening the meeting first)."
)


def keywords(question: str, limit: int = 6) -> list[str]:
    """The words worth searching for, longest first: names and nouns, not "what" or "the"."""
    words = re.findall(r"[\w'-]{3,}", question.lower())
    seen: list[str] = []
    for word in sorted(words, key=len, reverse=True):
        word = word.strip("'-")
        if len(word) >= 3 and word not in STOPWORDS and word not in seen:
            seen.append(word)
    return seen[:limit]


#: How many meetings are read in full (summary, action items, transcript) besides the
#: one on screen: those the search found, then the most recent.
READ_IN_FULL = 4


def gather(svc: Any, question: str, context: dict[str, Any]) -> str:
    """What Upshot looked up for ``question``, fenced as data, capped at GATHER_CHARS.

    A word search is not enough on its own: a question in Hebrew about a meeting held in
    English shares no word with it ("מה דנה צריכה לשלוח" and "Dana will send", checked
    2026-09-29), and the model cannot search again in other words. So the meetings the
    search found are read in full, and after them the most recent ones, as far as the
    budget goes; most questions are about those.
    """
    tools = AssistantTools(svc)
    parts: list[str] = []
    full: list[str] = []
    meeting = str(context.get("meeting_id") or "")
    if meeting and str(context.get("scope") or "meeting") == "meeting":
        full.append(meeting)
    for word in keywords(question):
        parts.append(tools.search(word, limit=15))
        for hit in svc.dao.search(word, limit=15):
            if hit.meeting_id not in full:
                full.append(hit.meeting_id)
    parts.append(tools.list_meetings(limit=15))
    parts.append(tools.list_action_items(open_only=True, limit=40))
    for recent in svc.dao.list_meetings(limit=READ_IN_FULL):
        if recent.id not in full:
            full.append(recent.id)
    # The meetings read in full come first: they carry the lines worth citing.
    reads: list[str] = []
    for meeting_id in full[: READ_IN_FULL + (1 if meeting else 0)]:
        reads.append(tools.get_meeting(meeting_id))
        reads.append(tools.get_transcript(meeting_id))
    out: list[str] = []
    used = 0
    for part in [*reads, *parts]:
        if used + len(part) > GATHER_CHARS:
            continue
        out.append(part)
        used += len(part)
    return "\n\n".join(out)


class AntigravityTranslator(Translator):
    """``agy``'s stream-json in, AI SDK chunks out: text deltas as they come, then a result."""

    def done(self, event: dict[str, Any]) -> bool:
        return event.get("event") == "result"

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("event")
        if kind == "init":
            self.turn.session_id = str(event.get("conversation_id") or self.turn.session_id)
            self.turn.model = str((event.get("init") or {}).get("model") or self.turn.model)
            return []
        if kind == "step_update":
            step = event.get("step_update") or {}
            if step.get("step_type") == "agent_response" and step.get("text_delta"):
                return self._emit_text(str(step["text_delta"]))
            return []
        if kind == "result":
            result = event.get("result") or {}
            chunks = self.close_text()
            if result.get("status") != "SUCCESS":
                self.turn.failed = True
                message = str(result.get("error") or result.get("status") or "failed")
                text = classify(1, message)
                code = problem_code(text)
                if code:
                    chunks.append(stream.data("problem", {"code": code, "provider": NAME}))
                chunks.append(stream.error(text))
            return chunks
        return []


def classify(code: int, said: str, path: str = antigravity_cli.DEFAULT_EXECUTABLE) -> str:
    """The same wording the summarizer uses for the same failures."""
    try:
        antigravity_cli.AntigravityCliClient._raise_for(code, said, "", path)
    except QuotaExhausted as exc:
        return str(exc)
    except (PermanentError, RecoverableError) as exc:
        return str(exc)
    return f"Antigravity exited {code}"


class AntigravityRoute(ClaudeRoute):
    name = NAME
    label = "Antigravity"

    def __init__(
        self, config: Any, *, command: list[str] | None = None, services: Any = None
    ) -> None:
        super().__init__(config, command=command)
        self.agy = antigravity_cli.AntigravityCliClient(config)
        self.cli = self.agy  # type: ignore[assignment]
        self.services = services
        #: The screen the question came from, for what to gather (set per turn by the API).
        self.context: dict[str, Any] = {}

    def env(self) -> dict[str, str]:
        return antigravity_cli.child_env()

    def cwd(self) -> str:
        # The folder whose .agents/agents/upshot.md makes the run tool-less (D79).
        return antigravity_cli.workdir()

    def translator(self, turn: Turn, citer: Citer | None) -> Translator:
        return AntigravityTranslator(turn, citer)

    def classify(self, code: int, err: str, path: str) -> str:
        return classify(code, err, path)

    def agy_args(self, base: list[str]) -> list[str]:
        model = ["--model", self.agy.model] if self.agy.model else []
        return [
            *base,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
            "--sandbox",
            "--agent",
            antigravity_cli.AGENT_NAME,
            *model,
        ]

    async def run(
        self,
        question: str,
        *,
        port: int,
        token: str,
        system: str,
        resume: str = "",
        turn: Turn | None = None,
        citer: Citer | None = None,
        recap: str = "",
    ) -> Any:
        turn = turn if turn is not None else Turn()
        base = self.executable()
        if base is None:
            turn.failed = True
            yield stream.data("problem", {"code": "not-installed", "provider": self.name})
            yield stream.error(
                "Antigravity is not installed. Install it from Settings and sign in, or "
                "choose another provider in Settings → AI."
            )
            return
        # Signed out, a run would open Google's sign-in page unasked and wait a minute.
        if not self.command and self.agy.account_status(base[0])[0] is False:
            turn.failed = True
            yield stream.data("problem", {"code": "signed-out", "provider": self.name})
            yield stream.error(
                "Antigravity is not signed in. Open Settings, AI agents, and press Sign in."
            )
            return
        found = gather(self.services, question, self.context) if self.services else ""
        prompt = system + NO_TOOLS
        if recap:
            prompt += f"\n\nThe conversation so far:\n{recap}"
        prompt += f"\n\nWhat Upshot looked up for this question:\n{found}"
        prompt += f"\n\nThe user's question:\n{question}"
        async for chunk in self._spawn(
            self.agy_args(base), antigravity_cli.user_event(prompt), turn, base[0], citer, ""
        ):
            yield chunk
