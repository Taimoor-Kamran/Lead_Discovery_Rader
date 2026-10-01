"""Which domain an audit asks DNS and RDAP about, and when it asks about none (v0.13.0, task 1).

A shared domain — a builder, a social platform, a link-in-bio page, a shortener — would
turn a fact about the platform into a finding about the business, so it is never asked.
"""

import pytest

from app.modules.audit_web.models import AuditStatus
from app.modules.domain_intel.selection import (
    LINK_IN_BIO_DOMAINS,
    SHARED_DOMAINS,
    SHORTENER_DOMAINS,
    DomainTarget,
    select,
)
from app.modules.normalization.web import BUILDER_DOMAINS, SOCIAL_DOMAINS


def test_registrable_domain_comes_from_the_public_suffix_list() -> None:
    # String splitting would give `co.uk`.
    chosen = select(
        status=AuditStatus.done, final_url="https://www.acme-plumbing.co.uk/", website=None
    )
    assert chosen.target == DomainTarget(
        site_host="www.acme-plumbing.co.uk", apex="acme-plumbing.co.uk"
    )
    assert chosen.skip_reason is None


def test_final_url_wins_over_the_configured_website() -> None:
    chosen = select(
        status=AuditStatus.done,
        final_url="https://www.new-name.com/home",
        website="http://old-name.com",
    )
    assert chosen.target == DomainTarget(site_host="www.new-name.com", apex="new-name.com")


def test_configured_website_is_used_when_there_is_no_final_url() -> None:
    chosen = select(status=AuditStatus.unreachable, final_url=None, website="old-name.com")
    assert chosen.target == DomainTarget(site_host="old-name.com", apex="old-name.com")


@pytest.mark.parametrize(
    "status", [AuditStatus.skipped, AuditStatus.robots_blocked, AuditStatus.failed]
)
def test_statuses_without_a_domain_question(status: AuditStatus) -> None:
    chosen = select(status=status, final_url=None, website="https://acme.com")
    assert chosen.target is None
    assert chosen.skip_reason == f"audit status {status.value}: no domain lookup"


@pytest.mark.parametrize("website", [None, "", "   "])
def test_no_website_is_skipped(website: str | None) -> None:
    chosen = select(status=AuditStatus.done, final_url=None, website=website)
    assert chosen.target is None
    assert chosen.skip_reason == "no website"


def test_a_host_with_no_registrable_domain_is_skipped() -> None:
    chosen = select(
        status=AuditStatus.done, final_url="https://demo-plumbing.invalid/", website=None
    )
    assert chosen.target is None
    assert chosen.skip_reason == "no registrable domain for demo-plumbing.invalid"


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.wixsite.com/plumbing",
        "https://topelectricianaustin.wixstudio.com",
        "https://acme.square.site",
        "https://www.facebook.com/acmeplumbing",
        "https://linktr.ee/acme",
        "https://beacons.ai/acme",
        "https://bit.ly/3abcde",
        "https://tinyurl.com/acme",
    ],
)
def test_shared_domains_are_skipped(url: str) -> None:
    chosen = select(status=AuditStatus.done, final_url=url, website=None)
    assert chosen.target is None
    assert chosen.skip_reason is not None
    assert chosen.skip_reason.endswith("is shared by many businesses")


def test_a_site_that_redirected_to_a_builder_is_skipped() -> None:
    chosen = select(
        status=AuditStatus.bot_challenge,
        final_url="https://acme.myshopify.com/",
        website="https://acme-plumbing.com",
    )
    assert chosen.target is None


def test_the_deny_list_reuses_the_normalization_lists() -> None:
    assert BUILDER_DOMAINS <= SHARED_DOMAINS
    assert SOCIAL_DOMAINS <= SHARED_DOMAINS
    assert LINK_IN_BIO_DOMAINS <= SHARED_DOMAINS
    assert SHORTENER_DOMAINS <= SHARED_DOMAINS


@pytest.mark.parametrize(
    "url", ["https://bücher.de/", "https://xn--bcher-kva.de/", "https://BÜCHER.de/"]
)
def test_one_internationalised_domain_gives_one_key(url: str) -> None:
    """IDNA: the Unicode, punycode and upper-case spellings are one `domain_intel` row."""
    chosen = select(status=AuditStatus.done, final_url=url, website=None)
    assert chosen.target == DomainTarget(site_host="xn--bcher-kva.de", apex="xn--bcher-kva.de")


def test_an_internationalised_www_host_keeps_its_www_and_shares_the_apex() -> None:
    chosen = select(status=AuditStatus.done, final_url="https://www.Bücher.de/", website=None)
    assert chosen.target == DomainTarget(site_host="www.xn--bcher-kva.de", apex="xn--bcher-kva.de")


def test_a_host_no_idna_codec_accepts_is_skipped_with_a_reason() -> None:
    """A zero-width joiner is not allowed in that position: there is no name to ask about."""
    chosen = select(
        status=AuditStatus.done, final_url="https://b\u00fccher\u200d.de/", website=None
    )
    assert chosen.target is None
    assert chosen.skip_reason == "b\u00fccher\u200d.de is not a valid domain name"


@pytest.mark.parametrize(
    "domain",
    [
        "vagaro.com",
        "phorest.com",
        "zenoti.com",
        "fresha.com",
        "booksy.com",
        "squareup.com",
        "setmore.com",
    ],
)
def test_a_booking_platform_page_as_the_website_asks_nothing(domain: str) -> None:
    chosen = select(
        status=AuditStatus.done, final_url=f"https://www.{domain}/acme-salon", website=None
    )
    assert chosen.target is None
    assert chosen.skip_reason == f"{domain} is shared by many businesses"
