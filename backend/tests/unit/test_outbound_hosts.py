"""No new outbound host may appear in the codebase without a spec that says so.

The blueprint's permitted-sources rule is the one that is easiest to break by accident and
hardest to spot in review: one `https://graph.facebook.com/…` in an adapter and the system
is reading social media. So the set of hosts the source code names is frozen here, with a
line per host saying what it is and which spec introduced it.

Adding a host to this list is the moment to ask whether the blueprint permits it — official
APIs, plus the business's own homepage within robots.txt. Nothing else belongs here.

Excluded from the scan, because they are not outbound hosts:
* reserved TLDs (`.invalid`, `.example`, `.test`) and `example.com` — documentation and
  fixture names, which by RFC 2606/6761 never resolve;
* `localhost` and `127.0.0.1` — this machine;
* `backend/app/demo/` — checked-in fixture *content* (fake homepages whose HTML links
  wherever the fiction says), never a URL this code requests;
* tests — recorded fixtures and the respx routes that stand in for them.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND_APP = REPO / "backend" / "app"
FRONTEND_SRC = REPO / "frontend" / "src"

URL = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9._-]*)")
RESERVED_SUFFIXES = (".invalid", ".example", ".test", ".localhost")
NOT_A_HOST = frozenset({"localhost", "127.0.0.1", "example.com", "www.example.com"})

# Every host the backend may name. One entry, one reason.
BACKEND_HOSTS = frozenset(
    {
        "places.googleapis.com",  # Google Places API (New) — discovery, v0.2.0
        "www.googleapis.com",  # PageSpeed Insights — website audit, v0.4.0
        "api.openai.com",  # OpenAI — classification, v0.5.0
        "api.airtable.com",  # Airtable — CRM export, v0.7.0
        # Documentation links only: terms and pricing shown to an operator, never fetched.
        "cloud.google.com",
        "developers.google.com",
        "openai.com",
        "airtable.com",
        "www.airtable.com",
        "www.google.com",
    }
)

# The web app talks to its own API and nothing else; these are link targets in help text.
FRONTEND_HOSTS = frozenset({"airtable.com"})


def hosts_in(root: Path, *suffixes: str, skip: tuple[Path, ...] = ()) -> dict[str, list[str]]:
    """Every host named in the source under `root`, mapped to the files that name it."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in suffixes or not path.is_file():
            continue
        if any(skipped in path.parents for skipped in skip):
            continue
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        for host in URL.findall(path.read_text(encoding="utf-8")):
            lowered = host.lower()
            if lowered in NOT_A_HOST or lowered.endswith(RESERVED_SUFFIXES):
                continue
            found.setdefault(lowered, []).append(str(path.relative_to(REPO)))
    return found


def test_the_backend_names_no_host_outside_the_frozen_set() -> None:
    found = hosts_in(BACKEND_APP, ".py", skip=(BACKEND_APP / "demo",))

    new = {host: files for host, files in found.items() if host not in BACKEND_HOSTS}
    assert not new, (
        f"new outbound host(s) {sorted(new)} in {sorted({f for fs in new.values() for f in fs})}. "
        "Permitted sources are official APIs plus the business's own homepage; if this is "
        "one, add it to BACKEND_HOSTS with the spec that introduced it."
    )


def test_the_frontend_names_no_host_outside_the_frozen_set() -> None:
    found = hosts_in(FRONTEND_SRC, ".ts", ".tsx")

    new = {host: files for host, files in found.items() if host not in FRONTEND_HOSTS}
    assert not new, f"new host(s) {sorted(new)} in the web app: {sorted(new.values())}"


def test_no_social_media_host_appears_anywhere_in_the_source() -> None:
    """The rule stated as itself, so it fails even if someone widens the sets above.

    The audit *records* which platforms a homepage links to, from patterns in
    `audit_web/fingerprints.py` — bare hostnames with no scheme, which is why they do not
    trip this. A `https://` in front of one of them would mean something is being fetched.
    """
    banned = (
        "facebook.com",
        "fb.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "reddit.com",
        "yelp.com",
        "nextdoor.com",
        "youtube.com",
    )
    for root, suffixes in ((BACKEND_APP, (".py",)), (FRONTEND_SRC, (".ts", ".tsx"))):
        skip = (BACKEND_APP / "demo",) if root is BACKEND_APP else ()
        for host, files in hosts_in(root, *suffixes, skip=skip).items():
            assert not host.endswith(banned), (
                f"{host} is requested in {files}. Social media is never read: "
                "the blueprint permits official APIs and the business's own homepage only."
            )
