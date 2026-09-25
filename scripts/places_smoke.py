"""Places Text Search smoke check: a few live calls, run by a human. Never part of the tests.

    uv run --project backend python scripts/places_smoke.py "electrician in Austin, TX"
    uv run --project backend python scripts/places_smoke.py "electrician in Austin, TX" \\
        --max-calls 6 --record backend/tests/fixtures/google_places/empty_page_recorded.json
    uv run --project backend python scripts/places_smoke.py "electrician in Austin, TX" \
        --limit 60 --max-calls 8

Follows `nextPageToken` from page to page, printing how many places each page held and
whether it carried a token, and stops at the first of: a page with no token, a page with
no places, `--max-calls`, or (with `--limit`) the limit. The default is one call; every
call is billed. Without `--limit` every page asks for 20; with it, each page asks for
only what is still wanted, the way discovery does.

Built to answer one question: past its last result, does Places answer 200 with no
places and a fresh token? That is what kept discovery paging until a cap stopped it.

Run from the repository root so the settings read `.env` there. The key is read the way
the application reads it and is never printed; the calls go through `app/core/http.py`
but not through the daily cap — like `psi_smoke.py`, this is an operator's tool, and the
count it prints is the whole of what it spent. `--record` writes an empty page only
(a page with places would put real listing content in the fixtures), and refuses to write
a file that contains the key.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.http import ApiHttpClient
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH

PAGE_SIZE = 20


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text_query")
    parser.add_argument("--max-calls", type=int, default=1, help="hard ceiling (default 1)")
    parser.add_argument("--record", type=Path, help="write the first empty page here")
    parser.add_argument(
        "--limit",
        type=int,
        help="ask each page for min(20, limit - places so far), as discovery does",
    )
    args = parser.parse_args()
    if args.max_calls < 1:
        print("--max-calls must be at least 1; nothing was called.", file=sys.stderr)
        return 2

    settings = get_settings()
    key = settings.google_places_api_key.get_secret_value()
    if not key:
        print("GOOGLE_PLACES_API_KEY is not set; nothing was called.", file=sys.stderr)
        return 2

    http = ApiHttpClient(source="google_places", secrets=[key], max_attempts=1)
    pages: list[dict[str, Any]] = []
    empty_page: dict[str, Any] | None = None
    token: str | None = None
    seen = 0
    stopped = "max-calls reached"
    try:
        while len(pages) < args.max_calls:
            page_size = PAGE_SIZE if args.limit is None else min(PAGE_SIZE, args.limit - seen)
            if page_size <= 0:
                stopped = "limit reached"
                break
            body: dict[str, Any] = {"textQuery": args.text_query, "pageSize": page_size}
            if token:
                body["pageToken"] = token
            payload: dict[str, Any] = http.request_json(
                "POST",
                f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}",
                headers={
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": settings.places_field_mask,
                    "Content-Type": "application/json",
                },
                json=body,
                parse=lambda raw: raw,
            )
            places = payload.get("places") or []
            token = payload.get("nextPageToken")
            seen += len(places)
            pages.append(
                {
                    "call": len(pages) + 1,
                    "page_size_sent": page_size,
                    "places": len(places),
                    "has_next_page_token": bool(token),
                    "response_keys": sorted(payload),
                }
            )
            if not places:
                empty_page = payload
                stopped = "empty page"
                break
            if not token:
                stopped = "no token"
                break
    finally:
        http.close()

    print(
        json.dumps(
            {
                "text_query": args.text_query,
                "limit": args.limit,
                "calls_made": len(pages),
                "places_total": sum(page["places"] for page in pages),
                "stopped_on": stopped,
                "pages": pages,
            },
            indent=2,
        )
    )

    if args.record:
        if empty_page is None:
            print("No empty page was seen; nothing was written.", file=sys.stderr)
            return 1
        text = json.dumps(empty_page, indent=2, ensure_ascii=False) + "\n"
        if key in text:
            print("The response contains the key; nothing was written.", file=sys.stderr)
            return 1
        args.record.write_text(text, encoding="utf-8")
        print(f"recorded {len(text)} bytes to {args.record}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
