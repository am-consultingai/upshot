"""Real Anthropic API calls. Excluded from the default run; skipped with no key.

These are never a prerequisite for a phase's exit criteria — they exist to measure the
numbers other decisions depend on (Hebrew tokens-per-word, cache hits).
"""

from __future__ import annotations

import json
import os

import pytest

from app.config import default_config
from app.llm.client import AnthropicClient, system_blocks
from app.llm.schema import NOTES_SCHEMA, validate
from app.llm.tokens import CachingCounter, tokens_per_word
from app.pipeline.stages.summarize import split_windows

pytestmark = pytest.mark.live_api

HEBREW_TRANSCRIPT = """**[00:00] THEM:** בוקר טוב, נתחיל עם הסטטוס של ה-deployment.
**[00:14] ME:** העברתי אתמול את השירות ל-Kubernetes, ויש עדיין בעיה עם ה-migration של בסיס הנתונים.
**[00:31] THEM:** כמה זמן זה ייקח לדעתך? אנחנו צריכים להחליט אם דוחים את הרילי‏ס לשבוע הבא.
**[00:47] ME:** אני מעריך יומיים. אני מציע שנדחה, ואני אחזור אליכם ביום חמישי עם עדכון.
**[01:05] THEM:** מקובל. אז ההחלטה היא לדחות את הרילי‏ס לשבוע הבא, ואתה מעדכן ביום חמישי.
"""


def _client() -> AnthropicClient:
    config = default_config()
    if not config.secret("anthropic", env="ANTHROPIC_API_KEY"):
        pytest.skip("no Anthropic API key in keyring or ANTHROPIC_API_KEY")
    return AnthropicClient(config)


def test_window_hebrew_ratio(capsys: pytest.CaptureFixture[str]) -> None:
    """Closes TECHNICAL-DESIGN §19.4 and becomes the regression bound."""
    client = _client()
    counter = CachingCounter(client.count_tokens)
    ratio = tokens_per_word(HEBREW_TRANSCRIPT, counter)
    print(f"hebrew tokens-per-word: {ratio:.3f}")
    with capsys.disabled():
        print(f"\nMEASUREMENT hebrew_tokens_per_word={ratio:.3f}")
    assert ratio < 5.0, f"Hebrew costs {ratio:.2f} tokens/word — re-derive the window size"


def test_cache_hit_on_second_window() -> None:
    """Zero cache reads on window 2 means something volatile leaked into the prefix."""
    client = _client()
    counter = CachingCounter(client.count_tokens)
    long_text = HEBREW_TRANSCRIPT * 40
    windows = split_windows(long_text, counter, target_tokens=2000, overlap_tokens=100)
    assert len(windows) >= 2
    blocks = system_blocks("You extract meeting notes." * 80, "Glossary: Kubernetes, ArgoCD")
    first = client.complete_json(system_blocks=blocks, user=windows[0].text, max_tokens=4000)
    second = client.complete_json(system_blocks=blocks, user=windows[1].text, max_tokens=4000)
    assert first.stop_reason == "end_turn"
    assert second.cache_read_tokens > 0, second.usage


def test_live_smoke() -> None:
    client = _client()
    result = client.complete_json(
        system_blocks=system_blocks("You write meeting notes as JSON.", None),
        user=HEBREW_TRANSCRIPT,
        schema=NOTES_SCHEMA,
        max_tokens=8000,
    )
    validate(result.data)
    assert result.stop_reason == "end_turn"
    assert result.data["title"].strip()


def test_cross_language_summary() -> None:
    """Hebrew audio → English notes is a first-class case, not a translation step."""
    from app.pipeline.stages.summarize import language_instruction

    client = _client()
    result = client.complete_json(
        system_blocks=system_blocks(
            "You write meeting notes as JSON.\n\n" + language_instruction("en"), None
        ),
        user=HEBREW_TRANSCRIPT,
        schema=NOTES_SCHEMA,
        max_tokens=8000,
    )
    tldr = " ".join(result.data["tldr"])
    hebrew = sum(1 for char in tldr if "֐" <= char <= "׿")
    assert hebrew / max(1, len(tldr)) < 0.10, f"tldr is not English: {tldr!r}"
    assert json.dumps(result.data, ensure_ascii=False)


@pytest.mark.skipif(not os.environ.get("MA_OLLAMA"), reason="set MA_OLLAMA=1 to test Ollama")
def test_local_model_path() -> None:
    from app.llm.client import OllamaClient

    client = OllamaClient(default_config())
    result = client.complete_json(
        system_blocks=system_blocks("You write meeting notes as JSON.", None),
        user=HEBREW_TRANSCRIPT,
    )
    validate(result.data)
