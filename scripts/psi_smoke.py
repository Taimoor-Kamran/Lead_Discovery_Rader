"""PageSpeed Insights smoke check: one live call, run by a human. Never part of the tests.

    uv run --project backend python scripts/psi_smoke.py https://example.com/
    uv run --project backend python scripts/psi_smoke.py https://example.com/ \\
        --record backend/tests/fixtures/pagespeed/runpagespeed_mobile_recorded.json

Run from the repository root so the settings read `.env` there. The key is read the way
the application reads it and is never printed; the call goes through `app/core/http.py`
like every official API call. `--record` writes the response for the test fixtures with
the base64 screenshots removed (they are most of the size and nothing reads them), and
refuses to write a file that contains the key.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.http import ApiHttpClient
from app.modules.audit_web.psi import (
    PAGESPEED_SOURCE_NAME,
    PSI_CATEGORIES,
    PSI_ENDPOINT,
    PSI_STRATEGY,
    parse_psi,
)

# Audits whose `details` carry base64 image data. Removed before recording, nothing else.
SCREENSHOT_AUDITS = ("final-screenshot", "full-page-screenshot", "screenshot-thumbnails")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--record", type=Path, help="write the trimmed response here")
    args = parser.parse_args()

    key = get_settings().pagespeed_api_key.get_secret_value()
    if not key:
        print("PAGESPEED_API_KEY is not set; nothing was called.", file=sys.stderr)
        return 2

    query = {
        "url": args.url,
        "strategy": PSI_STRATEGY,
        "category": list(PSI_CATEGORIES),
        "key": key,
    }
    http = ApiHttpClient(source=PAGESPEED_SOURCE_NAME, secrets=[key])
    try:
        payload: dict[str, Any] = http.request_json(
            "GET", f"{PSI_ENDPOINT}?{urlencode(query, doseq=True)}", parse=lambda body: body
        )
    finally:
        http.close()

    result = parse_psi(payload)
    print(
        json.dumps(
            {
                "url": args.url,
                "lighthouse_version": payload.get("lighthouseResult", {}).get("lighthouseVersion"),
                "categories_returned": sorted(
                    payload.get("lighthouseResult", {}).get("categories", {})
                ),
                **result.as_dict(),
            },
            indent=2,
        )
    )

    if args.record:
        audits = payload.get("lighthouseResult", {}).get("audits", {})
        for name in SCREENSHOT_AUDITS:
            audits.pop(name, None)
        payload.get("lighthouseResult", {}).pop("fullPageScreenshot", None)
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if key in text:
            print("The response contains the key; nothing was written.", file=sys.stderr)
            return 1
        args.record.write_text(text, encoding="utf-8")
        print(f"recorded {len(text)} bytes to {args.record}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
