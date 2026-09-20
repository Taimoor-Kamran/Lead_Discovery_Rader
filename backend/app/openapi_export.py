"""Write the API's OpenAPI document to a file. `make api-types` feeds it to `openapi-typescript`.

Run as `python -m app.openapi_export <path>`. Keys are sorted so the output, and therefore
the generated TypeScript, is byte-for-byte stable between runs: `make check` diffs it.
A file rather than stdout, because the app logs to stdout as it starts.
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m app.openapi_export <output.json>")
        return 2
    document = create_app().openapi()
    Path(args[0]).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
