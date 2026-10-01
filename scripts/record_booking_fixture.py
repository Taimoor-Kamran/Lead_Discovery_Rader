"""Record a homepage and its booking-link target as test fixtures. Run by a human.

    uv run --project backend python scripts/record_booking_fixture.py \\
        https://example.com/ atx_electrical

Writes `backend/tests/fixtures/pages/booking_<name>_home.html` and, when the booking check
matched a call to action or a path with a page behind it, `booking_<name>_target.html`.
Everything goes through `app/core/safe_fetch.py` (robots.txt, SSRF guard, per-host
throttle), exactly as an audit would. Two page requests at most; never part of the tests.
"""

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.safe_fetch import FetchOutcome, build_fetcher
from app.modules.audit_web import checks

PAGES = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "pages"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("name", help="fixture name, e.g. atx_electrical")
    args = parser.parse_args()
    fetcher = build_fetcher(settings=get_settings())

    def fetch(url: str) -> str | None:
        robots = fetcher.robots(url)
        if not robots.allowed:
            print(f"not fetched: {robots.reason}")
            return None
        outcome = fetcher.fetch(url)
        print(f"{url} -> HTTP {outcome.status_code} {outcome.error or ''}".rstrip())
        return outcome.text if outcome.reachable else None

    home = fetch(args.url)
    if home is None:
        return 1
    (PAGES / f"booking_{args.name}_home.html").write_text(home, encoding="utf-8")
    found, _ = checks.analyse_html(FetchOutcome(url=args.url, status_code=200, text=home))
    booking = found["booking"]
    print(f"booking: {booking.as_dict()}")
    if booking.method in checks.BOOKING_TO_FOLLOW and booking.target_url:
        target = fetch(booking.target_url)
        if target is not None:
            (PAGES / f"booking_{args.name}_target.html").write_text(target, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
