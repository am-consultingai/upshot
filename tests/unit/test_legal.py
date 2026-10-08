"""The Terms of Service: one source, its renderings, acceptance and updates (D83)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from app.clock import FakeClock
from app.config import Config
from app.legal.document import TermsError, parse
from app.legal.terms import TermsService, bundled

ROOT = Path(__file__).resolve().parents[2]

DOC = """---
version: {version}
effective: {effective}
material: {material}
summary: What changed.
---

# Upshot Terms of Service

## 1. Agreement {{#agreement}}

You accept **these** Terms. See the [Privacy Policy](https://example.com/privacy.html).

- one
- two
"""


def doc(version: str = "2026-11-01", effective: str = "2026-11-01", material: bool = True) -> str:
    return DOC.format(version=version, effective=effective, material=str(material).lower())


def service(
    tmp_path: Path,
    *,
    today: str = "2026-10-05",
    http: httpx.Client | None = None,
    installer: str | None = None,
) -> TermsService:
    config = Config.load(file=tmp_path / "app_config.json")
    clock = FakeClock(datetime.fromisoformat(f"{today}T09:00:00+03:00"))
    return TermsService(config, home=tmp_path, clock=clock, http=http, installer=lambda: installer)


# ------------------------------------------------------------------ the document


def test_the_bundled_terms_parse_with_a_version_and_unique_sections() -> None:
    terms = bundled()
    assert date.fromisoformat(terms.version) and date.fromisoformat(terms.effective)
    assert terms.material and terms.summary
    anchors = [s.anchor for s in terms.sections]
    assert len(anchors) == len(set(anchors)) and len(anchors) >= 20
    assert {"agreement", "licence", "plans", "updates", "changes", "law"} <= set(anchors)


def test_every_rendering_is_up_to_date_with_the_source() -> None:
    """An edit to app/legal/terms.md that was not rendered would ship two different texts."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_legal", ROOT / "scripts" / "build_legal.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0


def test_the_installer_text_is_ascii_with_windows_line_endings() -> None:
    raw = (ROOT / "packaging" / "terms.txt").read_bytes()
    raw.decode("ascii")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert raw.startswith(b"Upshot Terms of Service")


def test_the_manifest_names_the_published_copy_and_its_checksum() -> None:
    manifest = json.loads((ROOT / "site" / "legal" / "terms.json").read_text(encoding="utf-8"))
    published = parse((ROOT / "site" / "legal" / "terms.md").read_text(encoding="utf-8"))
    assert manifest["version"] == bundled().version == published.version
    assert manifest["sha256"] == published.sha256 == bundled().sha256


def test_rendering_escapes_and_keeps_links_and_bold() -> None:
    terms = parse(doc().replace("**these**", "**these** <script>x</script>"))
    html = terms.html()
    assert "<strong>these</strong>" in html and "&lt;script&gt;" in html
    assert '<a href="https://example.com/privacy.html" target="_blank" rel="noreferrer">' in html
    assert "<li>one</li>" in html
    text = terms.text()
    assert "Privacy Policy (https://example.com/privacy.html)" in " ".join(text.split())
    assert "**" not in text


@pytest.mark.parametrize(
    "broken",
    [
        "# no front matter\n\n## 1. A {#a}\n\nx\n",
        doc(version="soon"),
        doc(effective="2026-13-40"),
        doc().replace("## 1. Agreement {#agreement}", ""),
    ],
)
def test_a_document_the_app_cannot_trust_is_refused(broken: str) -> None:
    with pytest.raises((TermsError, ValueError)):
        parse(broken)


# ------------------------------------------------------------------ acceptance


def test_nothing_accepted_shows_the_terms_and_accepting_records_it(tmp_path: Path) -> None:
    terms = service(tmp_path)
    assert terms.state()["gate"] is True
    state = terms.accept(bundled().version)
    assert state["gate"] is False and state["notice"] is None
    assert state["accepted_via"] == "app"
    record = json.loads((tmp_path / "legal" / "consent.jsonl").read_text().splitlines()[-1])
    assert record == {
        **record,
        "version": bundled().version,
        "sha256": bundled().sha256,
        "via": "app",
    }
    reloaded = Config.load(file=tmp_path / "app_config.json")
    assert reloaded.get("legal.accepted_version") == bundled().version


def test_the_installer_licence_page_counts_once(tmp_path: Path) -> None:
    terms = service(tmp_path, installer=bundled().version)
    assert terms.adopt_installer_acceptance() is True
    assert terms.state()["gate"] is False
    assert terms.state()["accepted_via"] == "installer"
    assert terms.adopt_installer_acceptance() is False, "already recorded"


def test_an_installer_record_this_build_does_not_know_is_ignored(tmp_path: Path) -> None:
    terms = service(tmp_path, installer="2099-01-01")
    assert terms.adopt_installer_acceptance() is False
    assert terms.state()["gate"] is True


def test_acceptance_never_moves_backwards(tmp_path: Path) -> None:
    terms = service(tmp_path, today="2026-12-01")
    (tmp_path / "legal").mkdir()
    (tmp_path / "legal" / "terms-2026-11-01.md").write_text(doc(), encoding="utf-8")
    terms.accept("2026-11-01")
    with pytest.raises(ValueError):
        terms.accept(bundled().version)


@pytest.mark.parametrize(
    ("material", "today", "gate", "notice"),
    [
        (True, "2026-12-01", True, None),  # material and in effect: the whole screen
        (False, "2026-12-01", False, "changed"),  # minor and in effect: the banner
        (True, "2026-10-20", False, "upcoming"),  # announced before it applies
    ],
)
def test_a_newer_version_is_shown_the_way_its_kind_needs(
    tmp_path: Path, material: bool, today: str, gate: bool, notice: str | None
) -> None:
    terms = service(tmp_path, today=today)
    terms.accept(bundled().version)
    (tmp_path / "legal" / "terms-2026-11-01.md").write_text(
        doc(material=material), encoding="utf-8"
    )
    state = terms.state()
    assert state["gate"] is gate
    assert (state["notice"] or {}).get("kind") == notice


# ------------------------------------------------------------------ the website


def _website(
    manifest: dict[str, object] | None, source: str, *, fail: bool = False
) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("offline", request=request)
        if request.url.path.endswith("terms.json"):
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith("terms.md"):
            return httpx.Response(200, text=source)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handle))


def _manifest(source: str) -> dict[str, object]:
    terms = parse(source)
    return {
        "version": terms.version,
        "effective": terms.effective,
        "material": terms.material,
        "summary": terms.summary,
        "url": "terms.md",
        "sha256": terms.sha256,
    }


def test_a_newer_version_on_the_website_reaches_an_installed_copy(tmp_path: Path) -> None:
    source = doc()
    terms = service(tmp_path, today="2026-12-01", http=_website(_manifest(source), source))
    terms.accept(bundled().version)
    assert terms.check_now() is True
    assert (tmp_path / "legal" / "terms-2026-11-01.md").read_text(encoding="utf-8") == source
    assert terms.state()["gate"] is True and terms.state()["current"]["version"] == "2026-11-01"
    assert terms.check_now() is False, "nothing newer the second time"


def test_a_download_that_does_not_match_the_manifest_is_dropped(tmp_path: Path) -> None:
    source = doc()
    manifest = {**_manifest(source), "sha256": "0" * 64}
    terms = service(tmp_path, http=_website(manifest, source))
    assert terms.check_now() is False
    assert terms.last_error and "match" in terms.last_error
    assert (
        not list((tmp_path / "legal").glob("terms-*.md")) if (tmp_path / "legal").exists() else True
    )


def test_no_network_is_not_an_error_the_user_sees(tmp_path: Path) -> None:
    terms = service(tmp_path, http=_website(None, "", fail=True))
    assert terms.check_now() is False
    assert terms.last_error is not None
    assert terms.state()["current"]["version"] == bundled().version


def test_the_check_stays_off_when_disabled(tmp_path: Path) -> None:
    terms = service(tmp_path)
    terms.config.set("legal.check", False)
    terms.start()
    assert terms._thread is None


def test_the_notices_name_every_component_shipped_outside_a_package_manager() -> None:
    import importlib.util

    path = ROOT / "scripts" / "third_party_notices.py"
    spec = importlib.util.spec_from_file_location("third_party_notices", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.render()
    for name in (
        "FFmpeg",
        "ivrit-ai/whisper-large-v3-turbo-ct2",
        "Systran",
        "SIL OPEN FONT LICENSE",
    ):
        assert name.lower() in text.lower(), name
    # What else is listed depends on what this machine installed: the build machine has it
    # all, CI's Python job has no node_modules (2026-10-05).
    from importlib import metadata

    try:
        metadata.distribution("soxr")
    except metadata.PackageNotFoundError:
        pass
    else:
        assert "\nsoxr " in text and "Licence: LGPL-2.1-or-later" in text, (
            "soxr travels with its licence"
        )
    if (ROOT / "frontend" / "node_modules" / "react" / "package.json").exists():
        assert "\nreact " in text
