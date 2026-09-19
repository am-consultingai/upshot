"""Probe a Google Calendar "secret address in iCal format" — SECURITY-AND-AUTH.md §5.

    UP_ICAL_URL=https://calendar.google.com/calendar/ical/.../basic.ics \
        python3 scripts/probe-ical.py
    python3 scripts/probe-ical.py --url-file ~/.config/upshot/ical-url
    python3 scripts/probe-ical.py --watch 120 --for 3h

Two questions, one tool.

**Does it work at all** — status, size, how many events came back, and crucially *what
fields they carry*. Attendee display names are the input to Whisper's ``initial_prompt``,
which is where the Hebrew accuracy gain comes from, so "does the feed carry CN= names or
only mailto: addresses" decides how much this source is actually worth.

**Is it fresh enough to arm a recorder** — §5 records that Google's export "is cached to
an extent it doesn't document", and that single unknown decides whether a calendar can
arm the recorder ahead of a meeting or can only enrich one after the fact. ``--watch``
polls, hashes the body, and prints the wall-clock delay between an edit landing in Google
Calendar and appearing in the feed. Make an edit while it runs; it reports when it sees it.

The URL is a bearer credential for the whole calendar, so it is read from the environment
or a file, never a command-line argument (arguments show up in ``ps`` and shell history),
and it is never logged, printed or included in any output written here.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

FOLD = re.compile(r"\r?\n[ \t]")
DURATION = re.compile(r"^(\d+)([smhd])$")
SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: Response headers that speak to caching, which is the whole freshness question.
CACHE_HEADERS = ("age", "cache-control", "etag", "last-modified", "expires", "date")


def read_url(args: argparse.Namespace) -> str:
    if args.url_file:
        url = Path(args.url_file).expanduser().read_text(encoding="utf-8").strip()
    else:
        url = os.environ.get("UP_ICAL_URL", "").strip()
    if not url:
        sys.exit(
            "no URL. Put it in UP_ICAL_URL or pass --url-file <path>.\n"
            "It is a secret: do not pass it as a command-line argument."
        )
    if "/private-" not in url and "/public/" in url:
        print("! this looks like the PUBLIC address, not the secret one", file=sys.stderr)
    return url


def unfold(text: str) -> list[str]:
    """RFC 5545 folds long lines by inserting CRLF + one space. Undo that first."""
    return FOLD.sub("", text).splitlines()


def parse(lines: list[str]) -> dict[str, object]:
    """Count what the feed actually carries. Deliberately does not keep any titles."""
    events = 0
    with_summary = 0
    attendees = 0
    named_attendees = 0
    organizers = 0
    recurring = 0
    conference = 0
    starts: list[str] = []
    stamps: list[str] = []
    in_event = False

    for line in lines:
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        if line.startswith("BEGIN:VEVENT"):
            in_event, events = True, events + 1
            continue
        if line.startswith("END:VEVENT"):
            in_event = False
            continue
        if not in_event:
            continue
        if name == "SUMMARY":
            with_summary += 1
        elif name == "ATTENDEE":
            attendees += 1
            if "CN=" in line:
                named_attendees += 1
        elif name == "ORGANIZER":
            organizers += 1
        elif name == "RRULE":
            recurring += 1
        elif name in {"X-GOOGLE-CONFERENCE", "CONFERENCE"}:
            conference += 1
        elif name == "DTSTART":
            starts.append(line.split(":", 1)[-1])
        elif name in {"DTSTAMP", "LAST-MODIFIED"}:
            stamps.append(line.split(":", 1)[-1])

    return {
        "events": events,
        "with_summary": with_summary,
        "attendees": attendees,
        "named_attendees": named_attendees,
        "organizers": organizers,
        "recurring": recurring,
        "conference": conference,
        "earliest": min(starts) if starts else "-",
        "latest": max(starts) if starts else "-",
        "newest_stamp": max(stamps) if stamps else "-",
    }


def fetch(url: str) -> tuple[int, dict[str, str], str]:
    import httpx

    response = httpx.get(url, follow_redirects=True, timeout=30.0)
    return response.status_code, {k.lower(): v for k, v in response.headers.items()}, response.text


def report(url: str) -> str:
    """One fetch, printed. Returns the body hash so --watch can compare."""
    status, headers, body = fetch(url)
    digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:12]

    print(f"  HTTP {status}   {len(body):,} bytes   body-sha {digest}")
    if status != 200:
        print("  not 200 — a reset private URL, or the calendar was deleted")
        return digest
    if "BEGIN:VCALENDAR" not in body:
        print("  ! not an iCalendar document — check the URL")
        return digest

    stats = parse(unfold(body))
    print(
        f"  events {stats['events']}   with a title {stats['with_summary']}"
        f"   recurring {stats['recurring']}   with a meeting link {stats['conference']}"
    )
    named, total = stats["named_attendees"], stats["attendees"]
    share = f"{named}/{total}" if total else "none present"
    print(f"  attendees {share} carry a display name   organizers {stats['organizers']}")
    print(f"  event range {stats['earliest']} .. {stats['latest']}")
    print(f"  newest DTSTAMP/LAST-MODIFIED in feed: {stats['newest_stamp']}")
    caching = {k: headers[k] for k in CACHE_HEADERS if k in headers}
    print(f"  caching {caching or 'no cache headers at all'}")
    return digest


def seconds(text: str) -> int:
    match = DURATION.match(text)
    if not match:
        raise argparse.ArgumentTypeError("use forms like 90s, 5m, 3h, 1d")
    return int(match.group(1)) * SECONDS[match.group(2)]


def watch(url: str, every: int, limit: int) -> int:
    """Poll until the body changes, and say how long it took.

    Run it, then move an event in Google Calendar and note the time. The delta it
    prints is the number SECURITY-AND-AUTH.md §5 is missing.
    """
    print(f"watching every {every}s for up to {limit // 60} minutes.")
    print("make an edit in Google Calendar now; this prints when the feed catches up.\n")
    started = time.monotonic()
    baseline = report(url)
    print()

    changes = Counter()
    while time.monotonic() - started < limit:
        time.sleep(every)
        elapsed = int(time.monotonic() - started)
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        status, _, body = fetch(url)
        digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:12]
        if digest == baseline:
            print(f"  {stamp}  +{elapsed:>5}s  unchanged ({digest}, HTTP {status})")
            continue
        print(f"  {stamp}  +{elapsed:>5}s  CHANGED -> {digest}")
        changes[digest] += 1
        baseline = digest
        print()
        report(url)
        print()

    if not changes:
        print(
            "\nno change seen in the window. Either nothing was edited, or the feed "
            "is cached for longer than this run — both are findings worth writing down."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url-file", help="file holding the secret URL (not an argument)")
    parser.add_argument(
        "--watch", type=seconds, metavar="EVERY", help="poll this often, e.g. 120s or 5m"
    )
    parser.add_argument(
        "--for",
        dest="limit",
        type=seconds,
        default="1h",
        metavar="TOTAL",
        help="how long to keep watching (default 1h)",
    )
    args = parser.parse_args(argv)
    if isinstance(args.limit, str):
        args.limit = seconds(args.limit)

    url = read_url(args)
    if args.watch:
        return watch(url, args.watch, args.limit)
    return 0 if report(url) else 1


if __name__ == "__main__":
    raise SystemExit(main())
