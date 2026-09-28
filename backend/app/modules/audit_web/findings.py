"""The catalogue: which checks become a finding, and exactly how it is worded.

The wording rule is the point of this file. A finding is what a salesperson reads out to
a business owner, so it must be something the audit *saw*, never a verdict on the
business. "Audit found no online booking link on the homepage" is a fact anybody can
check; "needs a new website" is an opinion the data does not support. Every template
therefore starts with `Audit found`, `Audit could not`, `PageSpeed` or `Listing shows`,
and a test walks the whole catalogue to prove no banned word ever creeps in.

Each finding carries the same evidence triple the checks do, so nothing in the pipeline
downstream (scoring in v0.5.0, the review queue in v0.6.0, the CRM in v0.7.0) ever has to
take a finding on trust.
"""

import enum
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.modules.audit_web.checks import Checks, clip, value_of
from app.modules.audit_web.listing import MISMATCH, text_names_business
from app.modules.normalization import names
from app.modules.normalization.schemas import WebsiteKind
from app.modules.normalization.web import builder_host


class Severity(enum.StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"


class Service(enum.StrEnum):
    """The service a finding is filed under — the one mapping (spec v0.12.0, item 3).

    Until v0.12.0 a finding carried a coarse `service_category` (web_design, seo,
    performance, security…) while `opportunities/catalogue.py` kept its own finding →
    service table, and the two disagreed: `slow_mobile` said "performance" and was sold as
    website design. The catalogue below is now the only place a finding is mapped, and
    the opportunity catalogue derives its finding lists from it. Keys are the opportunity
    service keys; their human names live with the services, in `opportunities/catalogue.py`.
    """

    website_design = "website_design"
    seo_gbp = "seo_gbp"
    booking_setup = "booking_setup"
    ai_chat_setup = "ai_chat_setup"
    ads_social = "ads_social"


class Method(enum.StrEnum):
    """How a finding was established (spec v0.12.0, item 10).

    `deterministic` — our own code reading the homepage or the business record;
    `api` — a number an outside service measured (PageSpeed) or reported (the listing's
    review count); `ai` — a model's reading. No finding in this catalogue is `ai` today;
    the value exists so the queue can show one differently when there is.
    """

    deterministic = "deterministic"
    api = "api"
    ai = "ai"


# Every wording template must open with one of these, and contain none of the banned
# words. Enforced by `tests/unit/test_findings_wording.py` over the whole catalogue.
#
# A message never quotes the page or a contact detail: messages go to the AI step as they
# are, and only evidence is scrubbed of phones, emails and addresses (v0.12.0). So a title,
# a phone number or an email address is the *evidence* of a finding, never its wording.
ALLOWED_OPENINGS = ("Audit found", "Audit could not", "PageSpeed", "Listing shows")
BANNED_WORDS = ("needs", "should", "bad", "terrible", "outdated website")


@dataclass(frozen=True)
class FindingSpec:
    """One thing an audit can report, the service it is filed under, and its only wording."""

    code: str
    severity: Severity
    service: Service | None
    wording: str
    method: Method = Method.deterministic


@dataclass(frozen=True)
class Finding:
    """A reported spec, with the evidence for it."""

    code: str
    severity: Severity
    service: Service | None
    method: Method
    message: str
    evidence_text: str | None
    evidence_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "service": self.service.value if self.service is not None else None,
            "method": self.method.value,
            "message": self.message,
            "evidence_text": clip(self.evidence_text),
            "evidence_url": self.evidence_url,
        }


_DESIGN = Service.website_design
_SEO = Service.seo_gbp

CATALOGUE: dict[str, FindingSpec] = {
    spec.code: spec
    for spec in (
        FindingSpec(
            "no_website",
            Severity.high,
            _DESIGN,
            "Audit found no website on this business's listing.",
        ),
        FindingSpec(
            "social_profile_only",
            Severity.high,
            _DESIGN,
            "Audit found only a social media profile where a website would be, so no "
            "site was audited.",
        ),
        FindingSpec(
            "unreachable",
            Severity.high,
            _DESIGN,
            "Audit could not load the homepage at {url}.",
        ),
        FindingSpec(
            "no_https",
            Severity.high,
            _DESIGN,
            "Audit found the homepage served over http, not https.",
        ),
        FindingSpec(
            "tls_invalid",
            Severity.high,
            _DESIGN,
            "Audit could not verify the https certificate for {url}.",
        ),
        FindingSpec(
            "builder_subdomain",
            Severity.medium,
            _DESIGN,
            "Audit found the homepage served from a website-builder subdomain.",
        ),
        FindingSpec(
            "site_builder",
            Severity.low,
            _DESIGN,
            "Audit found the homepage is built with {platform}.",
        ),
        FindingSpec(
            "no_mobile_viewport",
            Severity.high,
            _DESIGN,
            "Audit found no mobile viewport tag on the homepage.",
        ),
        FindingSpec(
            "viewport_blocks_zoom",
            Severity.medium,
            _DESIGN,
            "Audit found the homepage's viewport tag asks phones not to let visitors zoom "
            "({detail}).",
        ),
        FindingSpec(
            "missing_title",
            Severity.medium,
            _SEO,
            "Audit found no page title on the homepage.",
        ),
        FindingSpec(
            "default_title",
            Severity.medium,
            _SEO,
            "Audit found the homepage title does not name the business.",
        ),
        FindingSpec(
            "short_title",
            Severity.low,
            _SEO,
            "Audit found the homepage title is only {length} characters long.",
        ),
        FindingSpec(
            "long_title",
            Severity.low,
            _SEO,
            "Audit found the homepage title is {length} characters long; search results "
            "show about the first 60.",
        ),
        FindingSpec(
            "missing_meta_description",
            Severity.medium,
            _SEO,
            "Audit found no meta description on the homepage.",
        ),
        FindingSpec(
            "no_h1",
            Severity.low,
            _SEO,
            "Audit found no main heading on the homepage.",
        ),
        FindingSpec(
            "multiple_h1",
            Severity.low,
            _SEO,
            "Audit found {count} main headings (h1) on the homepage, where one is usual.",
        ),
        FindingSpec(
            "no_structured_data",
            Severity.low,
            _SEO,
            "Audit found no LocalBusiness structured data on the homepage.",
        ),
        FindingSpec(
            "no_local_business_schema",
            Severity.low,
            _SEO,
            "Audit found structured data on the homepage ({types}), but none that describes "
            "a local business.",
        ),
        FindingSpec(
            "invalid_structured_data",
            Severity.medium,
            _SEO,
            "Audit found structured data on the homepage that does not parse, so search "
            "engines cannot read it.",
        ),
        # Low, and says why: call tracking is normal practice for these businesses, and the
        # canary found this on 7 of 17 plumbers (v0.12.0). It states a difference worth
        # confirming, never that the listing is wrong.
        FindingSpec(
            "nap_phone_mismatch",
            Severity.low,
            _SEO,
            "Audit found the homepage phone link differs from the listing's phone number; a "
            "call-tracking line is the usual explanation and is worth confirming.",
        ),
        FindingSpec(
            "nap_address_mismatch",
            Severity.medium,
            _SEO,
            "Audit found the address in the homepage's structured data differs from the "
            "listing's address in its {parts}.",
        ),
        FindingSpec(
            "listing_website_http",
            Severity.low,
            _SEO,
            "Listing shows the website as {listing}, an http address, while the homepage "
            "is served over https.",
        ),
        FindingSpec(
            "listing_website_host_mismatch",
            Severity.low,
            _SEO,
            "Listing shows the website as {listing}, while the homepage gives its own "
            "address as {site}.",
        ),
        FindingSpec(
            "no_online_booking",
            Severity.medium,
            Service.booking_setup,
            "Audit found no online booking or scheduling link on the homepage.",
        ),
        FindingSpec(
            "no_live_chat",
            Severity.medium,
            Service.ai_chat_setup,
            "Audit found no live chat or messaging widget on the homepage.",
        ),
        FindingSpec(
            "no_contact_on_homepage",
            Severity.high,
            _DESIGN,
            "Audit found no phone link, email link or contact form on the homepage.",
        ),
        FindingSpec(
            "no_click_to_call",
            Severity.medium,
            _DESIGN,
            "Audit found no click-to-call (tel:) link on the homepage.",
        ),
        FindingSpec(
            "placeholder_email",
            Severity.high,
            _DESIGN,
            "Audit found the homepage email link goes to a template placeholder domain, {domain}.",
        ),
        FindingSpec(
            "placeholder_text",
            Severity.medium,
            _DESIGN,
            'Audit found template placeholder text on the homepage: "{phrase}".',
        ),
        FindingSpec(
            "images_without_alt",
            Severity.low,
            _DESIGN,
            "Audit found {missing} of {total} images with no alt text on the homepage.",
        ),
        FindingSpec(
            "unlabelled_form_fields",
            Severity.low,
            _DESIGN,
            "Audit found {count} {noun} with no associated label on the homepage.",
        ),
        FindingSpec(
            "thin_content",
            Severity.low,
            _DESIGN,
            "Audit found {words} {noun} of visible text on the homepage.",
        ),
        FindingSpec(
            "no_section_headings",
            Severity.low,
            _DESIGN,
            "Audit found no section headings below the main heading.",
        ),
        FindingSpec(
            "heading_level_skipped",
            Severity.low,
            _DESIGN,
            "Audit found the homepage headings skip a level, from {higher} to {lower}.",
        ),
        FindingSpec(
            "stale_copyright",
            Severity.low,
            _DESIGN,
            "Audit found the homepage copyright year reads {year}.",
        ),
        FindingSpec(
            "future_copyright",
            Severity.medium,
            _DESIGN,
            "Audit found the homepage copyright year reads {year}, a year that has not "
            "happened yet.",
        ),
        FindingSpec(
            "slow_mobile",
            Severity.medium,
            _DESIGN,
            "PageSpeed Insights scored the homepage {score} out of 100 on mobile.",
            Method.api,
        ),
        FindingSpec(
            "low_accessibility_score",
            Severity.low,
            _DESIGN,
            "PageSpeed scored accessibility at {score} out of 100.",
            Method.api,
        ),
        FindingSpec(
            "low_best_practices_score",
            Severity.low,
            _DESIGN,
            "PageSpeed scored best practices at {score} out of 100.",
            Method.api,
        ),
        FindingSpec(
            "js_shell_suspected",
            Severity.info,
            _DESIGN,
            "Audit found almost no text in the homepage HTML, so this audit may be "
            "incomplete: the page appears to build itself with JavaScript, which this "
            "audit does not run.",
        ),
        FindingSpec(
            "few_reviews",
            Severity.low,
            _SEO,
            "Listing shows {count} {noun}.",
            Method.api,
        ),
        FindingSpec(
            "robots_blocked",
            Severity.info,
            None,
            "Audit could not read the homepage because robots.txt asks automated "
            "visitors to stay away.",
        ),
    )
}


def build(
    code: str,
    *,
    evidence_text: str | None = None,
    evidence_url: str | None = None,
    severity: Severity | None = None,
    **wording: object,
) -> Finding:
    """Instantiate one catalogue entry. An unknown code is a programming error.

    `severity` overrides the catalogue's only where a finding is graded by what it
    measured (the PageSpeed quality scores); the catalogue entry holds the mildest grade.
    """
    spec = CATALOGUE[code]
    return Finding(
        code=spec.code,
        severity=severity or spec.severity,
        service=spec.service,
        method=spec.method,
        message=spec.wording.format(**wording),
        evidence_text=evidence_text,
        evidence_url=evidence_url,
    )


# --- the rules --------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingContext:
    """Everything the rules below are allowed to look at."""

    industry: str | None
    website_kind: WebsiteKind
    website: str | None
    booking_industries: frozenset[str]
    slow_mobile_score: int
    stale_copyright_years: int
    now: datetime
    thin_content_words: int = 200
    quality_score_threshold: int = 90
    quality_score_medium_below: int = 70
    # The business's display name, for the title and placeholder rules. `None` turns off
    # the name comparison; the template-title list still applies.
    business_name: str | None = None


def for_missing_website(context: FindingContext, *, business_label: str) -> list[Finding]:
    """The two no-fetch cases. Neither makes a single network call."""
    if context.website_kind is WebsiteKind.social_profile:
        return [
            build(
                "social_profile_only",
                evidence_text=f"The listing for {business_label} gives {context.website}",
                evidence_url=context.website,
            )
        ]
    return [
        build(
            "no_website",
            # The only finding without an evidence URL: what it cites is our own record.
            evidence_text=f"The business record for {business_label} lists no website",
        )
    ]


def for_listing(
    user_rating_count: int | None, *, few_reviews: int, listing_url: str | None = None
) -> list[Finding]:
    """What the business's own listing says, whatever happened to its website.

    An unknown count is never a small one: `None` produces nothing. The evidence URL is
    the `source_url` of the listing record the count came from — a Google Maps place link
    for Places — and `None` only for a source that has no public page (the demo fixture).
    """
    if user_rating_count is None or user_rating_count >= few_reviews:
        return []
    return [
        build(
            "few_reviews",
            count=user_rating_count,
            noun="review" if user_rating_count == 1 else "reviews",
            evidence_text=f"The business listing reports {user_rating_count} user reviews",
            evidence_url=listing_url,
        )
    ]


def for_robots_blocked(reason: str, robots_url: str) -> list[Finding]:
    return [build("robots_blocked", evidence_text=reason, evidence_url=robots_url)]


def for_unreachable(checks: Checks, url: str, *, note: str | None = None) -> list[Finding]:
    """A site that did not answer. A certificate failure is reported as such, not as "down".

    `note` says how the answer was confirmed (v0.12.0): tried twice, with our own network
    up. It is appended to the evidence so a rep can say so if asked.
    """
    tls = checks.get("tls_valid")
    if tls is not None and tls.value is False:
        return [
            build(
                "tls_invalid",
                url=url,
                evidence_text=tls.evidence_text,
                evidence_url=tls.evidence_url or url,
            )
        ]
    reachable = checks.get("reachable")
    evidence = reachable.evidence_text if reachable is not None else None
    if note:
        evidence = f"{evidence}; {note}" if evidence else note
    return [build("unreachable", url=url, evidence_text=evidence, evidence_url=url)]


def for_page(
    checks: Checks, psi: Mapping[str, Any] | None, context: FindingContext
) -> list[Finding]:
    """Every finding a successfully read homepage produces, in catalogue order."""
    findings: list[Finding] = []
    url = value_of(checks, "final_url")

    if value_of(checks, "https") is False:
        findings.append(_from_check(checks, "https", "no_https"))

    host = builder_host(url)
    if host is not None:
        findings.append(
            build(
                "builder_subdomain",
                evidence_text=f"The homepage was served from {host}",
                evidence_url=url,
            )
        )

    if value_of(checks, "parsed"):
        findings.extend(_content_findings(checks, context))
        if host is None:
            findings.extend(_builder_findings(checks))
        findings.extend(_listing_findings(checks))

    if psi is not None:
        score = psi.get("performance_score")
        if isinstance(score, int | float) and score < context.slow_mobile_score:
            findings.append(
                build(
                    "slow_mobile",
                    score=int(score),
                    evidence_text=_psi_evidence(psi),
                    evidence_url=url,
                )
            )
        findings.extend(_quality_score_findings(psi, context, url))

    if value_of(checks, "js_shell_suspected"):
        findings.append(_from_check(checks, "js_shell_suspected", "js_shell_suspected"))
    return findings


def _content_findings(checks: Checks, context: FindingContext) -> list[Finding]:
    """Read from a page that was parsed, where every presence check answered true or false."""
    findings: list[Finding] = []
    if not value_of(checks, "viewport_meta"):
        findings.append(_from_check(checks, "viewport_meta", "no_mobile_viewport"))
    else:
        zoom = value_of(checks, "viewport_zoom_blocked")
        if isinstance(zoom, str) and zoom:
            findings.append(
                _from_check(checks, "viewport_zoom_blocked", "viewport_blocks_zoom", detail=zoom)
            )
    if not value_of(checks, "title"):
        findings.append(_from_check(checks, "title", "missing_title"))
    else:
        findings.extend(_title_findings(checks, context))
    if not value_of(checks, "meta_description"):
        findings.append(_from_check(checks, "meta_description", "missing_meta_description"))
    if value_of(checks, "h1_present") is False:
        findings.append(_from_check(checks, "h1_present", "no_h1"))
    h1_count = value_of(checks, "h1_count")
    if isinstance(h1_count, int) and not isinstance(h1_count, bool) and h1_count > 1:
        findings.append(_from_check(checks, "h1_count", "multiple_h1", count=h1_count))
    findings.extend(_structured_data_findings(checks))

    no_contact = not any(
        value_of(checks, key) for key in ("tel_link", "mailto_link", "contact_form")
    )
    if no_contact:
        findings.append(_from_check(checks, "contact_form", "no_contact_on_homepage"))
    elif value_of(checks, "tel_link") is False:
        # Only when the page offers some other way in: with no contact option at all,
        # `no_contact_on_homepage` already says there is no phone link.
        findings.append(_from_check(checks, "tel_link", "no_click_to_call"))

    mailto = value_of(checks, "mailto_address")
    if isinstance(mailto, dict) and mailto.get("placeholder"):
        findings.append(
            _from_check(
                checks,
                "mailto_address",
                "placeholder_email",
                domain=str(mailto["address"]).rpartition("@")[2],
            )
        )
    phrase = _placeholder_phrase(checks, context)
    if phrase is not None:
        findings.append(_from_check(checks, "placeholder_text", "placeholder_text", phrase=phrase))

    industry = (context.industry or "").lower()
    if not value_of(checks, "booking") and industry in context.booking_industries:
        findings.append(_from_check(checks, "booking", "no_online_booking"))

    if value_of(checks, "live_chat") is False:
        findings.append(_from_check(checks, "live_chat", "no_live_chat"))

    findings.extend(_page_quality_findings(checks, context))

    year = value_of(checks, "copyright_year")
    # `bool` is a subclass of `int`, and "no copyright notice" is `False`: without the
    # second half of this test an absent notice would be read as the year zero and
    # reported as stale.
    if isinstance(year, int) and not isinstance(year, bool):
        if year > context.now.year:
            findings.append(_from_check(checks, "copyright_year", "future_copyright", year=year))
        elif year <= context.now.year - context.stale_copyright_years:
            findings.append(_from_check(checks, "copyright_year", "stale_copyright", year=year))
    return findings


# Titles a template or a builder ships with, compared a segment at a time: "Home | Business"
# is two template segments. Lowercase.
TEMPLATE_TITLE_SEGMENTS = frozenset(
    {
        "home",
        "homepage",
        "home page",
        "welcome",
        "untitled",
        "untitled document",
        "untitled page",
        "site",
        "my site",
        "my website",
        "website",
        "my wordpress site",
        "wordpress",
        "just another wordpress site",
        "new page",
        "new site",
        "index",
        "business",
        "my business",
        "company",
        "page",
        "default",
        "blank",
        "document",
        "main",
        "coming soon",
        "under construction",
        "react app",
    }
)
_TITLE_SEPARATORS = re.compile(r"\s*[|\-\u2013\u2014:\u2022\u00b7\u00bb/]\s*")
SHORT_TITLE_CHARS = 20
LONG_TITLE_CHARS = 60


def _title_findings(checks: Checks, context: FindingContext) -> list[Finding]:
    """A title that is a template's, or names nothing of the business; or is too short/long.

    The general case is "the title carries no word of the business name" (matched leniently,
    see `listing.text_names_business`); the template list catches the rest, such as "Home"
    on a business whose name contains the word "home".
    """
    title = str(value_of(checks, "title"))
    segments = [part.lower() for part in _TITLE_SEPARATORS.split(title) if part.strip()]
    template = bool(segments) and all(part in TEMPLATE_TITLE_SEGMENTS for part in segments)
    names_business = text_names_business(title, context.business_name)
    findings: list[Finding] = []
    if template or names_business is False:
        findings.append(_from_check(checks, "title", "default_title"))
    elif len(title) < SHORT_TITLE_CHARS:
        findings.append(_from_check(checks, "title", "short_title", length=len(title)))
    if len(title) > LONG_TITLE_CHARS:
        findings.append(_from_check(checks, "title", "long_title", length=len(title)))
    return findings


def _structured_data_findings(checks: Checks) -> list[Finding]:
    """The four states `no_structured_data` used to cover alone (v0.12.0, item 1).

    * a LocalBusiness item in any syntax — nothing to report;
    * a JSON-LD block that does not parse — `invalid_structured_data`, and nothing about
      absence: the business has tried, and "none" would be false;
    * other types only — `no_local_business_schema`, naming them;
    * nothing at all — `no_structured_data`.
    """
    findings: list[Finding] = []
    errors = value_of(checks, "structured_data_errors")
    broken = isinstance(errors, dict) and bool(errors.get("count"))
    if broken:
        findings.append(_from_check(checks, "structured_data_errors", "invalid_structured_data"))
    if value_of(checks, "structured_data") or broken:
        return findings
    types = value_of(checks, "structured_data_types") or []
    if types:
        findings.append(
            _from_check(
                checks,
                "structured_data_types",
                "no_local_business_schema",
                types=", ".join(str(name) for name in types[:5]),
            )
        )
    else:
        findings.append(_from_check(checks, "structured_data", "no_structured_data"))
    return findings


def _placeholder_phrase(checks: Checks, context: FindingContext) -> str | None:
    """The first placeholder phrase that is not simply part of the business's own name."""
    phrases = value_of(checks, "placeholder_text")
    if not isinstance(phrases, list):
        return None
    # Every word of the name, generic ones included: "Your Company Store" really is
    # called "Your Company", and its footer saying so is not a placeholder.
    own = set((names.normalize_name(context.business_name) or "").split())
    for phrase in phrases:
        words = str(phrase).lower().split()
        if own and all(word in own for word in words):
            continue
        return str(phrase)
    return None


def _builder_findings(checks: Checks) -> list[Finding]:
    """A website builder recognised on the business's own domain (v0.12.0, item 6)."""
    tech = value_of(checks, "tech_stack")
    builder = tech.get("builder") if isinstance(tech, dict) else None
    if not isinstance(builder, dict) or not builder.get("label"):
        return []
    return [
        build(
            "site_builder",
            platform=builder["label"],
            evidence_text=str(builder.get("evidence") or ""),
            evidence_url=value_of(checks, "final_url"),
        )
    ]


def _listing_findings(checks: Checks) -> list[Finding]:
    """Where the homepage and the listing disagree. Only a `mismatch` is ever reported."""
    comparison = value_of(checks, "listing_comparison")
    if not isinstance(comparison, dict):
        return []
    url = value_of(checks, "final_url")
    findings: list[Finding] = []

    phone = comparison.get("phone") or {}
    if phone.get("status") == MISMATCH:
        findings.append(
            build(
                "nap_phone_mismatch",
                evidence_text=(
                    f"Homepage tel: link {phone.get('site_raw') or phone['site']}; "
                    f"listing phone {phone['listing']}"
                ),
                evidence_url=url,
            )
        )

    address = comparison.get("address") or {}
    if address.get("status") == MISMATCH:
        parts = list(address.get("differs_in") or [])
        findings.append(
            build(
                "nap_address_mismatch",
                parts=_and_list(parts),
                evidence_text=(
                    f"Homepage structured data: {address['site']}; listing: {address['listing']}"
                ),
                evidence_url=url,
            )
        )

    website = comparison.get("website") or {}
    if website.get("status") == MISMATCH:
        differs = website.get("differs_in") or []
        evidence = f"Listing website {website['listing']}; homepage {website['site']}"
        if "http" in differs:
            findings.append(
                build(
                    "listing_website_http",
                    listing=website["listing"],
                    evidence_text=evidence,
                    evidence_url=url,
                )
            )
        if "www" in differs:
            findings.append(
                build(
                    "listing_website_host_mismatch",
                    listing=website["listing"],
                    site=website["site"],
                    evidence_text=evidence,
                    evidence_url=url,
                )
            )
    return findings


def _and_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


QUALITY_SCORES = (
    ("accessibility_score", "low_accessibility_score", "accessibility"),
    ("best_practices_score", "low_best_practices_score", "best practices"),
)


def _quality_score_findings(
    psi: Mapping[str, Any], context: FindingContext, url: str | None
) -> list[Finding]:
    """Lighthouse's category scores, reported below the threshold. A null is never a zero.

    Graded by the score itself (a decision made during the v0.11.0 build): below
    `quality_score_medium_below` (70) is `medium`, from there up to the threshold (90) is
    `low`, and at or above the threshold nothing is reported.
    """
    findings: list[Finding] = []
    for key, code, label in QUALITY_SCORES:
        score = psi.get(key)
        if isinstance(score, bool) or not isinstance(score, int | float):
            continue
        if score < context.quality_score_threshold:
            findings.append(
                build(
                    code,
                    score=int(score),
                    severity=(
                        Severity.medium
                        if score < context.quality_score_medium_below
                        else Severity.low
                    ),
                    evidence_text=(
                        f"PageSpeed Insights scored {label} {int(score)}/100 "
                        f"({psi.get('strategy') or 'mobile'})"
                    ),
                    evidence_url=url,
                )
            )
    return findings


def _page_quality_findings(checks: Checks, context: FindingContext) -> list[Finding]:
    """Counts of things present in the document. Each finding quotes what it counted."""
    findings: list[Finding] = []

    images = value_of(checks, "images_without_alt")
    if isinstance(images, dict) and images.get("without_alt"):
        findings.append(
            _from_check(
                checks,
                "images_without_alt",
                "images_without_alt",
                missing=images["without_alt"],
                total=images["images"],
            )
        )

    fields = value_of(checks, "unlabelled_inputs")
    if isinstance(fields, dict) and fields.get("unlabelled"):
        count = int(fields["unlabelled"])
        findings.append(
            _from_check(
                checks,
                "unlabelled_inputs",
                "unlabelled_form_fields",
                count=count,
                noun="form field" if count == 1 else "form fields",
            )
        )

    # A page that builds itself with JavaScript has already been reported as such; its
    # word count describes our fetch, not the page a visitor reads.
    words = value_of(checks, "word_count")
    if (
        isinstance(words, int)
        and not isinstance(words, bool)
        and words < context.thin_content_words
        and not value_of(checks, "js_shell_suspected")
    ):
        findings.append(
            _from_check(
                checks,
                "word_count",
                "thin_content",
                words=words,
                noun="word" if words == 1 else "words",
            )
        )

    headings = value_of(checks, "heading_structure")
    if isinstance(headings, dict):
        if headings.get("sections_below_h1") == 0:
            check = checks["heading_structure"]
            findings.append(
                build(
                    "no_section_headings",
                    evidence_text=headings.get("first_h1") or check.evidence_text,
                    evidence_url=check.evidence_url or value_of(checks, "final_url"),
                )
            )
        skips = headings.get("skips") or []
        if skips:
            higher, _, lower = str(skips[0]).partition(" to ")
            findings.append(
                _from_check(
                    checks, "heading_structure", "heading_level_skipped", higher=higher, lower=lower
                )
            )
    return findings


def _from_check(checks: Checks, check_key: str, code: str, **wording: Any) -> Finding:
    """Build a finding from the check that triggered it, carrying its evidence over."""
    check = checks.get(check_key)
    return build(
        code,
        evidence_text=check.evidence_text if check is not None else None,
        evidence_url=(check.evidence_url if check is not None else None)
        or value_of(checks, "final_url"),
        **wording,
    )


def _psi_evidence(psi: Mapping[str, Any]) -> str:
    """The measured numbers, so a score is never just an assertion."""
    parts = [f"mobile performance {psi.get('performance_score')}/100"]
    for label, key, unit in (
        ("LCP", "lcp_ms", " ms"),
        ("CLS", "cls", ""),
        ("TBT", "tbt_ms", " ms"),
    ):
        value = psi.get(key)
        if value is not None:
            parts.append(f"{label} {value}{unit}")
    crux = psi.get("crux_category")
    if crux:
        parts.append(f"CrUX {crux}")
    return f"PageSpeed Insights measured {', '.join(parts)}"


def codes(findings: Iterable[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def as_payload(findings: Iterable[Finding]) -> list[dict[str, Any]]:
    return [finding.as_dict() for finding in findings]
