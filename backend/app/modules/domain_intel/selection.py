"""Which domain an audit asks about — or why it asks about none (spec v0.13.0, task 1).

The registrable domain comes from the Public Suffix List, never from splitting the host on
dots: `example.co.uk` is a domain, `co.uk` is not. A host whose registrable domain is shared
by many businesses — a site builder, a social platform, a link-in-bio page, a URL shortener
— says nothing about this business, so it is skipped rather than reported on: an expired
`wixsite.com` would be a fact about Wix.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.modules.audit_web.models import AuditStatus
from app.modules.normalization.web import (
    BUILDER_DOMAINS,
    SOCIAL_DOMAINS,
    builder_host,
    registered_domain,
)

# One page of links on someone else's domain. `linktr.ee` is already a social domain.
LINK_IN_BIO_DOMAINS = frozenset(
    {"beacons.ai", "lnk.bio", "bio.link", "campsite.bio", "taplink.cc", "linkin.bio"}
)
# A redirect service. A website that is a short link names the service, not the business.
SHORTENER_DOMAINS = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "rebrand.ly",
        "is.gd",
        "cutt.ly",
        "shorturl.at",
        "tiny.cc",
        "bl.ink",
    }
)
SHARED_DOMAINS = BUILDER_DOMAINS | SOCIAL_DOMAINS | LINK_IN_BIO_DOMAINS | SHORTENER_DOMAINS

# Statuses whose audit asks no domain question at all. `skipped` had no site to ask about;
# on `robots_blocked` the owner said not to crawl, and no domain finding is emitted there
# (task 5), so a lookup would spend an RDAP call on nothing. `failed` is a fault on our
# side — often our own network — so a DNS answer then would be about us, not the domain.
NO_DOMAIN_STATUSES = frozenset(
    {AuditStatus.skipped, AuditStatus.robots_blocked, AuditStatus.failed}
)


class SkipReason:
    """Why an audit's `checks.domain_intel` is null. Stored as the check's evidence text."""

    audit_status = "audit status {status}: no domain lookup"
    no_website = "no website"
    no_registrable_domain = "no registrable domain for {host}"
    shared_domain = "{domain} is shared by many businesses"


@dataclass(frozen=True)
class DomainTarget:
    """What to ask about: the host the site was served from, and its registrable domain."""

    site_host: str
    apex: str


@dataclass(frozen=True)
class Selection:
    target: DomainTarget | None
    skip_reason: str | None = None


def select(*, status: AuditStatus, final_url: str | None, website: str | None) -> Selection:
    """The domain this audit asks about, from `final_url` when present, else `website`."""
    if status in NO_DOMAIN_STATUSES:
        return Selection(None, SkipReason.audit_status.format(status=status.value))
    url = final_url or website
    host = _host(url)
    if host is None:
        return Selection(None, SkipReason.no_website)
    apex = registered_domain(host)
    if apex is None:
        return Selection(None, SkipReason.no_registrable_domain.format(host=host))
    if apex in SHARED_DOMAINS or builder_host(url) is not None:
        return Selection(None, SkipReason.shared_domain.format(domain=apex))
    return Selection(DomainTarget(site_host=host, apex=apex))


def _host(url: str | None) -> str | None:
    """The lowercase host exactly as served — `www.` kept, since that is the name asked."""
    if not url or not url.strip():
        return None
    candidate = url.strip()
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    host = urlsplit(candidate).hostname
    if not host or "." not in host:
        return None
    return host.lower().rstrip(".") or None
