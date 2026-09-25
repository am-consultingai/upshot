"""What the assistant may look at. Read-only by construction (D61): every function here
reads the database or a meeting's files and returns text for the model.

The same functions are served to the Claude Code and Codex CLIs over MCP
(``app/assistant/mcp.py``), so a tool is written once whichever CLI calls it.
"""

from __future__ import annotations

import json
from typing import Any

from app.services import Services

#: Tool output is data, never instructions (D61). Step 6 of the plan randomises the
#: delimiter per request; the fixed pair is enough for the model to know where it ends.
DATA_OPEN = "<upshot-data>"
DATA_CLOSE = "</upshot-data>"


def as_data(payload: Any) -> str:
    return f"{DATA_OPEN}\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n{DATA_CLOSE}"


class AssistantTools:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    def _titles(self) -> dict[str, tuple[str, str]]:
        meetings = self.svc.dao.list_meetings(limit=10_000)
        return {m.id: (m.title or "", m.started_at or "") for m in meetings}

    def search(self, query: str, limit: int = 20) -> str:
        """Meetings, action items and transcript lines that contain the words."""
        limit = max(1, min(int(limit), 50))
        hits = self.svc.dao.search(query, limit=limit)
        titles = self._titles()
        results = [
            {
                "meeting_id": hit.meeting_id,
                "meeting_title": titles.get(hit.meeting_id, ("", ""))[0],
                "meeting_date": titles.get(hit.meeting_id, ("", ""))[1],
                "kind": hit.kind,
                "speaker": hit.speaker,
                "at_ms": hit.at_ms,
                "text": hit.text,
            }
            for hit in hits
        ]
        return as_data({"query": query, "count": len(results), "results": results})
