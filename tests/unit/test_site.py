"""The product pages in ``site/`` make claims, and claims drift.

Every figure on a page that the code can answer is checked against the code here:
the version, the number of summary providers, the retention default. The rest are
the pages' own hygiene rules — anchors that resolve, no resources from anywhere but
the brand CDN and Google Fonts, and every example visibly marked as demo data — so a
later edit cannot quietly break them. Each rule runs against the English page and the
Hebrew one.
"""

from __future__ import annotations

import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.config import _ENUMS, DEFAULTS

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
PAGES = (SITE / "index.html", SITE / "he" / "index.html")
LEGAL = (SITE / "privacy.html", SITE / "terms.html")

ALLOWED_HOSTS = (
    "https://cdn.jsdelivr.net/gh/am-consultingai/am-assets@v2/",
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
)

#: How each page labels invented data.
DEMO_LABEL = {"index.html": "Demo data", "he/index.html": "נתוני הדגמה"}


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []
        self.loads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(str(a["id"]))
        if tag == "a" and a.get("href"):
            self.hrefs.append(str(a["href"]))
        # Anything the browser fetches on load, as opposed to a link it follows.
        for key in ("src", "srcset"):
            if a.get(key):
                self.loads.append(str(a[key]))
        if tag == "link" and a.get("href") and a.get("rel") != "alternate":
            self.loads.append(str(a["href"]))


def _parse(page: Path) -> tuple[str, _Collect]:
    html = page.read_text(encoding="utf-8")
    collected = _Collect()
    collected.feed(html)
    return html, collected


def _numbers(html: str, attr: str) -> set[str]:
    return set(re.findall(rf"<[^>]*\b{attr}\b[^>]*>(\d+)<", html))


each_page = pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(SITE)))
every_page = pytest.mark.parametrize("page", PAGES + LEGAL, ids=lambda p: str(p.relative_to(SITE)))


@each_page
def test_version_matches_pyproject(page: Path) -> None:
    html, _ = _parse(page)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert re.findall(r"data-version>([^<]+)<", html) == [version]


@each_page
def test_provider_count_matches_config(page: Path) -> None:
    """``fake`` is a test double, not something a user can pick."""
    html, _ = _parse(page)
    real = [p for p in _ENUMS["llm.provider"] if p != "fake"]
    assert _numbers(html, "data-providers") == {str(len(real))}


@each_page
def test_audio_retention_matches_config(page: Path) -> None:
    html, _ = _parse(page)
    assert _numbers(html, "data-audio-days") == {str(DEFAULTS["retention"]["audio_days"])}


@every_page
def test_in_page_anchors_resolve(page: Path) -> None:
    _, collected = _parse(page)
    missing = [h for h in collected.hrefs if h.startswith("#") and h[1:] not in collected.ids]
    assert not missing


@every_page
def test_local_files_exist(page: Path) -> None:
    _, collected = _parse(page)
    local = [u for u in collected.loads if not u.startswith("https://")]
    missing = [u for u in local if not (page.parent / u).resolve().is_file()]
    assert local and not missing


@every_page
def test_only_allowed_external_resources(page: Path) -> None:
    _, collected = _parse(page)
    external = [u for u in collected.loads if u.startswith(("http:", "https:", "//"))]
    assert all(u.startswith(ALLOWED_HOSTS) for u in external), external


@every_page
def test_brand_assets_are_pinned(page: Path) -> None:
    html, _ = _parse(page)
    assert "@main" not in html
    assert "am-favicon" not in html  # a product page carries its own favicon


@each_page
def test_metadata_present(page: Path) -> None:
    html, _ = _parse(page)
    for needle in ("<title>", 'name="description"', 'property="og:image"', 'property="og:title"'):
        assert needle in html
    assert 'hreflang="he"' in html and 'hreflang="en"' in html
    assert (SITE / "og.png").is_file()


@each_page
def test_examples_are_marked_as_demo(page: Path) -> None:
    """Every illustration that invents data says so where it stands.

    A diagram is exempt: the benchmark chart states a measured source, and the hero
    flow draws where a meeting goes. Neither puts words in anyone's mouth.
    """
    html, _ = _parse(page)
    label = DEMO_LABEL[str(page.relative_to(SITE))]
    diagrams = ('class="wer"', 'class="flow"')
    figures = [
        f
        for f in re.findall(r"<figure\b[^>]*>.*?</figure>", html, flags=re.S)
        if not any(d in f for d in diagrams)
    ]
    assert figures and all(label in f for f in figures)
    # The screenshot and the example email are not figures, but are invented too.
    assert html.count(label) >= len(figures) + 2


def test_pages_link_to_each_other() -> None:
    english, hebrew = (p.read_text(encoding="utf-8") for p in PAGES)
    assert 'href="he/"' in english and 'data-lang="he"' in english
    assert 'href="../"' in hebrew and 'data-lang="en"' in hebrew


def test_homepages_link_to_the_legal_pages() -> None:
    """Google's OAuth review requires the privacy policy to be linked from the homepage."""
    for page in PAGES:
        footer = page.read_text(encoding="utf-8").split("<footer>")[1]
        assert "privacy.html" in footer and "terms.html" in footer


def test_privacy_policy_has_what_google_review_checks() -> None:
    """The items Google's OAuth verification looks for, each of which is easy to lose in an edit."""
    policy = (SITE / "privacy.html").read_text(encoding="utf-8")
    required = (
        # the Limited Use disclosure, in Google's own wording, with the policy linked
        "use and transfer to any other app of information received from Google APIs will "
        "adhere to the",
        "https://developers.google.com/terms/api-services-user-data-policy",
        "including the Limited Use requirements",
        # the exact scope, and what is done with it
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "generalized or non-personalized artificial-intelligence or machine-learning models",
        "do not sell Google user data",
        # how to revoke, and who to contact
        "https://myaccount.google.com/permissions",
        "office@amconsultingai.com",
        'id="google"',
    )
    missing = [r for r in required if r not in policy]
    assert not missing


def test_terms_name_the_operator_and_law() -> None:
    terms = (SITE / "terms.html").read_text(encoding="utf-8")
    for needle in ("AM Consulting", "State of Israel", "office@amconsultingai.com", "privacy.html"):
        assert needle in terms


def test_site_stays_small() -> None:
    total = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    assert total < 1_000_000
