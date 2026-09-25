"""Websites: what the business actually has, and what may be compared across records.

Two hosts sharing a website builder are not the same business, and a social profile is
not a website at all — both would otherwise merge half of a city into one record.
"""

from urllib.parse import urlsplit, urlunsplit

import tldextract

from app.modules.normalization.schemas import WebsiteKind

# Registered domains whose subdomains belong to different businesses. For these the
# whole host is the identity, so `foo.wixsite.com` never matches `bar.wixsite.com`.
BUILDER_DOMAINS = frozenset(
    {
        "wixsite.com",
        "square.site",
        "godaddysites.com",
        "business.site",
        "weebly.com",
        "webflow.io",
        "carrd.co",
        # Added in v0.11.0. `wixstudio.com` is the one seen on a real run: without it
        # `topelectricianaustin.wixstudio.com` read as an own site whose identity was the
        # whole of `wixstudio.com`. The rest are the same shape — one subdomain per
        # customer on a builder's own domain.
        "wixstudio.com",
        "editorx.io",
        "weeblysite.com",
        "squarespace.com",
        "wordpress.com",
        "mystrikingly.com",
        "jimdosite.com",
        "site123.me",
        "myshopify.com",
    }
)
# A profile on someone else's platform. It is recorded, but it is never an identity:
# `domain` stays null so two businesses cannot merge on "both are on Facebook".
SOCIAL_DOMAINS = frozenset(
    {
        "facebook.com",
        "fb.com",
        "instagram.com",
        "linktr.ee",
        "yelp.com",
        "nextdoor.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "linkedin.com",
    }
)

# Offline on purpose: no suffix list is ever downloaded, and no cache directory is read
# or written. The snapshot bundled with tldextract is the only source of truth.
_extract = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True, cache_dir=None)


def host_of(raw: str | None) -> str | None:
    """The lowercase host of a URL, without `www.` or a port. `None` when there is none."""
    if not raw or not raw.strip():
        return None
    candidate = raw.strip()
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    host = urlsplit(candidate).hostname
    if not host or "." not in host:
        return None
    host = host.lower().rstrip(".")
    return host.removeprefix("www.") or None


def registered_domain(host: str) -> str | None:
    result = _extract(host)
    return result.top_domain_under_public_suffix or None


def parse_website(raw: str | None) -> tuple[str | None, str | None, WebsiteKind]:
    """Return `(website, domain, website_kind)` for one raw website value.

    `website` keeps the URL as it was given, minus any query string or fragment.
    `domain` is the comparable identity — the registered domain for a normal site, the
    whole host for a site-builder subdomain, and `None` for a social profile.
    """
    host = host_of(raw)
    if host is None:
        return None, None, WebsiteKind.none

    website = _clean_url(raw)
    registered = registered_domain(host)
    if registered is None:
        # A suffix the bundled snapshot does not know: a brand-new TLD, an intranet
        # name, or a reserved one such as `.invalid`. The whole host is then the
        # identity, which is the spec's own fallback rule and can only under-merge.
        return website, host, WebsiteKind.own_site
    if registered in SOCIAL_DOMAINS:
        return website, None, WebsiteKind.social_profile
    if registered in BUILDER_DOMAINS:
        return website, host, WebsiteKind.builder_subdomain
    return website, registered, WebsiteKind.own_site


def builder_host(url: str | None) -> str | None:
    """The host of `url` when it is a subdomain of a website builder, else `None`.

    Read from the host itself rather than from a stored `website_kind`, so it answers for
    the URL a page was actually *served* from: a builder subdomain that redirects to the
    business's own domain is not one, and a record classified before a builder was added
    to the list is still recognised.
    """
    host = host_of(url)
    if host is None:
        return None
    for builder in BUILDER_DOMAINS:
        if host.endswith(f".{builder}"):
            return host
    return None


def _clean_url(raw: str | None) -> str | None:
    """Keep scheme, host and path exactly as given; drop the query and the fragment."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    parts = urlsplit(value if "//" in value else f"//{value}")
    trimmed = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return trimmed.removeprefix("//") or value
