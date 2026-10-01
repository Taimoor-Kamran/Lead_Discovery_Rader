"""RDAP: four facts, nothing about a person, `null` on any doubt (spec v0.13.0, task 3).

Every call goes through `respx`; no test touches a live registry. The registrant-contacts
fixture is synthetic (its own `_comment` says so): the shape of a real Verisign answer, with
every contact value fabricated.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.core.rdap import (
    RDAP_MAX_ATTEMPTS,
    RDAP_TIMEOUT,
    NetworkRdapClient,
    OfflineRdapClient,
    bootstrap,
    build_rdap_client,
    network_rdap_client,
    parse_domain,
)
from tests.conftest import FakeClock, load_fixture

VERISIGN = "https://rdap.verisign.com/com/v1/"
EXAMPLE_URL = f"{VERISIGN}domain/example.com"
CONTACTS_URL = f"{VERISIGN}domain/synthetic-contacts.com"
# Every fabricated contact value in the synthetic fixture, and the registrar's own
# address and phone: none of it may survive the parse boundary.
CONTACT_VALUES = (
    "Pat Fabricated-Owner",
    "pat.fabricated@example.com",
    "5555550123",
    "123 Fabricated Street",
    "Fabricated Plumbing Co",
    "Sam Fabricated-Admin",
    "sam.fabricated@example.com",
    "456 Invented Avenue",
    "Lee Fabricated-Tech",
    "lee.fabricated@example.com",
    "789 Madeup Lane",
    "Abuse Desk Fabricated",
    "abuse-fabricated@example.com",
    "5555550100",
    "99 Registrar Fabricated Way",
    "5555550199",
    "Springfield",
)


def real() -> Any:
    return load_fixture("rdap", "verisign_example.com.json")


def contacts() -> Any:
    return load_fixture("rdap", "synthetic_registrant_contacts.json")


def _settings(**overrides: Any) -> Settings:
    return Settings(jwt_secret=SecretStr("x" * 32), environment="ci", **overrides)


def client(**overrides: Any) -> NetworkRdapClient:
    clock = FakeClock()
    return network_rdap_client(
        _settings(**overrides), fakeredis.FakeStrictRedis(), clock=clock, sleeper=clock.sleep
    )


# --- endpoints -------------------------------------------------------------------------


def test_bootstrap_snapshot_is_dated_and_https_only() -> None:
    endpoints = bootstrap()
    assert endpoints.publication == "2026-09-28T22:00:03Z"
    assert all(url.startswith("https://") for url in endpoints.services.values())


@pytest.mark.parametrize(
    ("domain", "base"),
    [
        ("acme.com", VERISIGN),
        ("acme.net", "https://rdap.verisign.com/net/v1/"),
        ("acme.co.uk", "https://rdap.nominet.uk/uk/"),
        ("acme.not-a-real-tld", None),
    ],
)
def test_endpoint_is_chosen_by_tld(domain: str, base: str | None) -> None:
    assert bootstrap().base_url(domain) == base


def test_a_tld_with_no_endpoint_asks_nothing(mock_http: respx.MockRouter) -> None:
    result = client().lookup("acme.not-a-real-tld")
    assert result.facts is None
    assert result.reason == "no RDAP endpoint for this TLD"
    assert not mock_http.calls


# --- the parse boundary ------------------------------------------------------------------


def test_the_four_facts_from_a_real_answer() -> None:
    facts = parse_domain(real())
    assert facts is not None
    assert facts.registrar == "RESERVED-Internet Assigned Numbers Authority"
    assert facts.created == datetime(1995, 8, 14, 4, 0, tzinfo=UTC)
    assert facts.expires == datetime(2027, 8, 13, 4, 0, tzinfo=UTC)
    assert facts.status == (
        "client delete prohibited",
        "client transfer prohibited",
        "client update prohibited",
    )


def test_only_the_registrar_name_survives_from_any_entity() -> None:
    facts = parse_domain(contacts())
    assert facts is not None
    assert facts.registrar == "Synthetic Registrar, LLC"
    text = repr(facts)
    for value in CONTACT_VALUES:
        assert value not in text


def test_no_registrar_entity_is_a_null_registrar() -> None:
    body = real()
    body["entities"] = [
        {"roles": ["registrant"], "vcardArray": ["vcard", [["fn", {}, "text", "X"]]]}
    ]
    facts = parse_domain(body)
    assert facts is not None
    assert facts.registrar is None


def test_no_expiry_date_is_nothing() -> None:
    body = real()
    body["events"] = [e for e in body["events"] if e["eventAction"] != "expiration"]
    assert parse_domain(body) is None


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "text",
        {"events": "nope"},
        {"events": [{"eventAction": "expiration", "eventDate": "not a date"}]},
    ],
)
def test_a_strange_shape_is_nothing_and_never_raises(body: Any) -> None:
    assert parse_domain(body) is None


# --- the client ------------------------------------------------------------------------


def test_a_lookup_returns_facts_and_the_url_asked(mock_http: respx.MockRouter) -> None:
    route = mock_http.get(EXAMPLE_URL).mock(return_value=httpx.Response(200, json=real()))
    result = client().lookup("example.com")
    assert result.facts is not None
    assert result.url == EXAMPLE_URL
    request = route.calls.last.request
    assert request.headers["Accept"] == "application/rdap+json"
    assert request.url.scheme == "https"
    assert request.extensions["timeout"]["read"] == RDAP_TIMEOUT.read == 10.0


def test_the_stored_snapshot_carries_provenance_and_no_contacts(
    mock_http: respx.MockRouter,
) -> None:
    mock_http.get(CONTACTS_URL).mock(return_value=httpx.Response(200, json=contacts()))
    result = client().lookup("synthetic-contacts.com")
    snapshot = result.snapshot(checked_at=datetime(2026, 10, 1, tzinfo=UTC))
    assert snapshot == {
        "source": "rdap",
        "source_url": CONTACTS_URL,
        "bootstrap_publication": "2026-09-28T22:00:03Z",
        "checked_at": "2026-10-01T00:00:00+00:00",
        "registrar": "Synthetic Registrar, LLC",
        "created": "2012-03-02T17:11:09+00:00",
        "expires": "2027-03-02T17:11:09+00:00",
        "status": [
            "client delete prohibited",
            "client transfer prohibited",
            "client update prohibited",
        ],
    }


@pytest.mark.parametrize("status", [404, 400, 403, 500, 503])
def test_an_error_answer_is_null_after_one_attempt(
    mock_http: respx.MockRouter, status: int
) -> None:
    route = mock_http.get(EXAMPLE_URL).mock(return_value=httpx.Response(status))
    result = client().lookup("example.com")
    assert result.facts is None
    assert result.snapshot(checked_at=datetime.now(UTC)) is None
    assert route.call_count == RDAP_MAX_ATTEMPTS == 1


def test_a_timeout_is_null(mock_http: respx.MockRouter) -> None:
    mock_http.get(EXAMPLE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    result = client().lookup("example.com")
    assert result.facts is None
    assert result.reason == "RDAP lookup failed (TransientError)"


def test_an_answer_without_expiry_is_null(mock_http: respx.MockRouter) -> None:
    body = real()
    body["events"] = []
    mock_http.get(EXAMPLE_URL).mock(return_value=httpx.Response(200, json=body))
    result = client().lookup("example.com")
    assert result.facts is None
    assert result.reason == "RDAP answer had no expiry date"


def test_the_daily_cap_stops_the_call(mock_http: respx.MockRouter) -> None:
    route = mock_http.get(EXAMPLE_URL).mock(return_value=httpx.Response(200, json=real()))
    rdap = client(rdap_daily_call_cap=1)
    assert rdap.lookup("example.com").facts is not None
    capped = rdap.lookup("example.com")
    assert capped.facts is None
    assert capped.reason == "RDAP lookup failed (QuotaExceededError)"
    assert route.call_count == 1


def test_a_failure_logs_no_part_of_the_body(
    mock_http: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    mock_http.get(CONTACTS_URL).mock(return_value=httpx.Response(418, json=contacts()))
    with caplog.at_level(logging.DEBUG):
        result = client().lookup("synthetic-contacts.com")
    assert result.facts is None
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for value in CONTACT_VALUES:
        assert value not in logged


def test_development_and_ci_never_ask_a_live_registry(mock_http: respx.MockRouter) -> None:
    rdap = build_rdap_client(settings=_settings())
    assert isinstance(rdap, OfflineRdapClient)
    assert rdap.lookup("example.com").facts is None
    assert not mock_http.calls
