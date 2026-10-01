"""The smoke script's recording: redacted, and replaying to the same snapshot (v0.13.0, task 9).

The script itself is run by a human against live DNS and RDAP; here it is driven with a
fixture resolver and `respx`, so nothing leaves the machine.
"""

import importlib.util
import io
import json
import pathlib
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import httpx
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.core.dns import FixtureResolver
from app.core.http import ApiHttpClient
from app.core.rdap import RDAP_SOURCE_NAME
from app.modules.domain_intel import dns_lookup
from app.modules.domain_intel.selection import DomainTarget
from tests.conftest import load_fixture
from tests.unit.test_rdap import CONTACT_VALUES

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "domain_intel_smoke.py"
DOMAIN = "synthetic-contacts.com"
TARGET = DomainTarget(site_host=f"www.{DOMAIN}", apex=DOMAIN)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
RUA = "rua=mailto:pat.fabricated@example.com"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("domain_intel_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ok(*values: str) -> dict[str, Any]:
    return {"rcode": "NOERROR", "values": list(values), "resolver": "192.0.2.53"}


def live_like() -> FixtureResolver:
    """What a live resolver might say, personal mailbox and all."""
    return FixtureResolver(
        {
            f"www.{DOMAIN}": {"A": _ok("192.0.2.10"), "AAAA": _ok(), "CNAME": _ok()},
            DOMAIN: {
                "A": _ok("192.0.2.10"),
                "AAAA": _ok(),
                "NS": _ok("ns1.domaincontrol.com."),
                "MX": _ok(),
                "TXT": _ok("v=spf1 include:x.com +all", "owner=pat.fabricated@example.com"),
            },
            f"_dmarc.{DOMAIN}": {"TXT": _ok(f"v=DMARC1; p=none; {RUA}; ruf=mailto:x@y.com")},
        }
    )


def test_recording_is_redacted_and_replays_to_the_same_snapshot(
    mock_http: respx.MockRouter, tmp_path: pathlib.Path
) -> None:
    smoke = load_script()
    mock_http.get(f"https://rdap.verisign.com/com/v1/domain/{DOMAIN}").mock(
        return_value=httpx.Response(
            200, json=load_fixture("rdap", "synthetic_registrant_contacts.json")
        )
    )
    out = io.StringIO()
    settings = Settings(jwt_secret=SecretStr("x" * 40), environment="ci", bot_contact="x")

    calls = smoke.check_one(
        f"https://www.{DOMAIN}/",
        resolver=live_like(),
        http=ApiHttpClient(source=RDAP_SOURCE_NAME, max_attempts=1),
        settings=settings,
        site_answered=False,
        record=tmp_path,
        now=NOW,
        out=out,
    )

    printed = out.getvalue()
    assert calls == 1
    for code in ("spf_allows_all", "dmarc_policy_none", "no_domain_mx"):
        assert code in printed
    written = [
        (tmp_path / "dns" / f"recorded_{DOMAIN}.json").read_text(encoding="utf-8"),
        (tmp_path / "rdap" / f"recorded_{DOMAIN}.json").read_text(encoding="utf-8"),
    ]
    for text in [*written, printed]:
        for value in CONTACT_VALUES:
            assert value not in text
        assert "mailto" not in text

    recorded = json.loads(written[0])["answers"]
    replay = FixtureResolver({name.lower(): types for name, types in recorded.items()})
    again = dns_lookup.lookup(replay, TARGET, budget_seconds=5, now=NOW)
    live = dns_lookup.lookup(live_like(), TARGET, budget_seconds=5, now=NOW)
    assert again["spf"] == live["spf"] and again["dmarc"] == live["dmarc"]
    assert again["txt_other_count"] == live["txt_other_count"] == 1
    rdap = json.loads(written[1])
    assert [e["roles"] for e in rdap["entities"]] == [["registrar"]]


def test_a_shared_domain_is_skipped_and_asks_nothing(mock_http: respx.MockRouter) -> None:
    smoke = load_script()
    resolver = live_like()
    out = io.StringIO()

    calls = smoke.check_one(
        "https://acme.wixsite.com/home",
        resolver=resolver,
        http=ApiHttpClient(source=RDAP_SOURCE_NAME, max_attempts=1),
        settings=Settings(jwt_secret=SecretStr("x" * 40), environment="ci", bot_contact="x"),
        site_answered=False,
        record=None,
        now=NOW,
        out=out,
    )

    assert calls == 0 and resolver.queries == [] and not mock_http.calls
    assert "skipped" in out.getvalue()
