"""Render the Terms from ``app/legal/terms.md`` into everything that shows them (D83).

    uv run python scripts/build_legal.py          # write the outputs
    uv run python scripts/build_legal.py --check  # fail if any output is out of date

Writes:

- ``site/terms.html``: the part between ``<!-- terms:begin -->`` and ``<!-- terms:end -->``
  (version line, contents, sections). The page around it is hand-written.
- ``site/legal/terms.md`` and ``site/legal/terms.json``: the published copy and the
  manifest installed apps read once a day (``legal.manifest_url``). Publishing a new
  version of the Terms is: edit ``app/legal/terms.md``, run this, push. GitHub Pages does
  the rest, and every installed copy shows it within a day.
- ``packaging/terms.txt``: the installer's licence page (``LicenseFile``).

``tests/unit/test_legal.py`` runs the check, so an edit to the Terms that was not
rendered fails the suite instead of shipping two different texts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.legal.document import Terms, parse  # noqa: E402

SOURCE = ROOT / "app" / "legal" / "terms.md"
PAGE = ROOT / "site" / "terms.html"
PUBLISHED = ROOT / "site" / "legal" / "terms.md"
MANIFEST = ROOT / "site" / "legal" / "terms.json"
INSTALLER = ROOT / "packaging" / "terms.txt"
BEGIN, END = "<!-- terms:begin -->", "<!-- terms:end -->"


def page_block(terms: Terms) -> str:
    return (
        f"{BEGIN}\n"
        f'  <p class="legal-links">Version {terms.version} · effective {terms.effective}</p>\n\n'
        '  <nav class="toc" aria-label="Contents">\n    <ol>\n'
        f"{terms.toc_html()}"
        "    </ol>\n  </nav>\n\n"
        f"{terms.html()}"
        f"  {END}"
    )


def outputs(terms: Terms) -> dict[Path, str]:
    page = PAGE.read_text(encoding="utf-8")
    start, end = page.find(BEGIN), page.find(END)
    if start < 0 or end < start:
        raise SystemExit(f"{PAGE} has no {BEGIN} … {END} block")
    manifest = {
        "version": terms.version,
        "effective": terms.effective,
        "material": terms.material,
        "summary": terms.summary,
        "url": "terms.md",
        "sha256": terms.sha256,
        "page": "https://upshot.amconsultingai.com/terms.html",
    }
    return {
        PAGE: page[:start] + page_block(terms) + page[end + len(END) :],
        PUBLISHED: terms.source,
        MANIFEST: json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        # The installer reads a plain-text licence file in the ANSI code page; keep it ASCII.
        INSTALLER: _ascii(terms.text()),
    }


def _ascii(text: str) -> str:
    for fancy, plain in (
        ("“", '"'),
        ("”", '"'),
        ("‘", "'"),
        ("’", "'"),
        ("–", "-"),
        ("—", "-"),
        ("·", "-"),
    ):
        text = text.replace(fancy, plain)
    text.encode("ascii")  # anything else is a mistake worth failing on
    return text.replace("\n", "\r\n")


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if an output is out of date")
    args = parser.parse_args(argv)
    terms = parse(SOURCE.read_text(encoding="utf-8"))
    stale = []
    for path, content in outputs(terms).items():
        current = path.read_bytes().decode("utf-8") if path.exists() else None
        # Line endings are git's business, not a difference: a Windows checkout has CRLF
        # where this writes LF (the build on machine A, 2026-10-05).
        if current is not None and _lf(current) == _lf(content):
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
    if args.check and stale:
        print("out of date (run scripts/build_legal.py):", *stale, sep="\n  ")
        return 1
    if not args.check:
        print(f"terms {terms.version}: " + (", ".join(map(str, stale)) or "nothing changed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
