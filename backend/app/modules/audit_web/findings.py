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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.modules.audit_web.checks import Checks, clip, value_of
from app.modules.normalization.schemas import WebsiteKind
from app.modules.normalization.web import builder_host


class Severity(enum.StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"


class ServiceCategory(enum.StrEnum):
    """Which service a gap points at. `None` where a finding is about the audit itself."""

    web_design = "web_design"
    seo = "seo"
    booking = "booking"
    performance = "performance"
    ecommerce = "ecommerce"
    security = "security"
    web_presence = "web_presence"
    chat = "chat"


# Every wording template must open with one of these, and contain none of the banned
# words. Enforced by `tests/unit/test_findings_wording.py` over the whole catalogue.
ALLOWED_OPENINGS = ("Audit found", "Audit could not", "PageSpeed", "Listing shows")
BANNED_WORDS = ("needs", "should", "bad", "terrible", "outdated website")


@dataclass(frozen=True)
class FindingSpec:
    """One thing an audit can report, and the only wording it may be reported in."""

    code: str
    severity: Severity
    service_category: ServiceCategory | None
    wording: str


@dataclass(frozen=True)
class Finding:
    """A reported spec, with the evidence for it."""

    code: str
    severity: Severity
    service_category: ServiceCategory | None
    message: str
    evidence_text: str | None
    evidence_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "service_category": (
                self.service_category.value if self.service_category is not None else None
            ),
            "message": self.message,
            "evidence_text": clip(self.evidence_text),
            "evidence_url": self.evidence_url,
        }


CATALOGUE: dict[str, FindingSpec] = {
    spec.code: spec
    for spec in (
        FindingSpec(
            "no_website",
            Severity.high,
            ServiceCategory.web_presence,
            "Audit found no website on this business's listing.",
        ),
        FindingSpec(
            "social_profile_only",
            Severity.high,
            ServiceCategory.web_presence,
            "Audit found only a social media profile where a website would be, so no "
            "site was audited.",
        ),
        FindingSpec(
            "unreachable",
            Severity.high,
            ServiceCategory.web_design,
            "Audit could not load the homepage at {url}.",
        ),
        FindingSpec(
            "no_https",
            Severity.high,
            ServiceCategory.security,
            "Audit found the homepage served over http, not https.",
        ),
        FindingSpec(
            "tls_invalid",
            Severity.high,
            ServiceCategory.security,
            "Audit could not verify the https certificate for {url}.",
        ),
        FindingSpec(
            "builder_subdomain",
            Severity.medium,
            ServiceCategory.web_design,
            "Audit found the homepage served from a website-builder subdomain.",
        ),
        FindingSpec(
            "no_mobile_viewport",
            Severity.high,
            ServiceCategory.web_design,
            "Audit found no mobile viewport tag on the homepage.",
        ),
        FindingSpec(
            "missing_title",
            Severity.medium,
            ServiceCategory.seo,
            "Audit found no page title on the homepage.",
        ),
        FindingSpec(
            "missing_meta_description",
            Severity.medium,
            ServiceCategory.seo,
            "Audit found no meta description on the homepage.",
        ),
        FindingSpec(
            "no_h1",
            Severity.low,
            ServiceCategory.seo,
            "Audit found no main heading on the homepage.",
        ),
        FindingSpec(
            "no_structured_data",
            Severity.low,
            ServiceCategory.seo,
            "Audit found no LocalBusiness structured data on the homepage.",
        ),
        FindingSpec(
            "no_online_booking",
            Severity.medium,
            ServiceCategory.booking,
            "Audit found no online booking or scheduling link on the homepage.",
        ),
        FindingSpec(
            "no_live_chat",
            Severity.medium,
            ServiceCategory.chat,
            "Audit found no live chat or messaging widget on the homepage.",
        ),
        FindingSpec(
            "no_contact_on_homepage",
            Severity.high,
            ServiceCategory.web_design,
            "Audit found no phone link, email link or contact form on the homepage.",
        ),
        FindingSpec(
            "images_without_alt",
            Severity.low,
            ServiceCategory.web_design,
            "Audit found {missing} of {total} images with no alt text on the homepage.",
        ),
        FindingSpec(
            "unlabelled_form_fields",
            Severity.low,
            ServiceCategory.web_design,
            "Audit found {count} {noun} with no associated label on the homepage.",
        ),
        FindingSpec(
            "thin_content",
            Severity.low,
            ServiceCategory.web_design,
            "Audit found {words} {noun} of visible text on the homepage.",
        ),
        FindingSpec(
            "no_section_headings",
            Severity.low,
            ServiceCategory.web_design,
            "Audit found no section headings below the main heading.",
        ),
        FindingSpec(
            "heading_level_skipped",
            Severity.low,
            ServiceCategory.web_design,
            "Audit found the homepage headings skip a level, from {higher} to {lower}.",
        ),
        FindingSpec(
            "stale_copyright",
            Severity.low,
            ServiceCategory.web_design,
            "Audit found the homepage copyright year reads {year}.",
        ),
        FindingSpec(
            "slow_mobile",
            Severity.medium,
            ServiceCategory.performance,
            "PageSpeed Insights scored the homepage {score} out of 100 on mobile.",
        ),
        FindingSpec(
            "low_accessibility_score",
            Severity.low,
            ServiceCategory.web_design,
            "PageSpeed scored accessibility at {score} out of 100.",
        ),
        FindingSpec(
            "low_best_practices_score",
            Severity.low,
            ServiceCategory.web_design,
            "PageSpeed scored best practices at {score} out of 100.",
        ),
        FindingSpec(
            "js_shell_suspected",
            Severity.info,
            ServiceCategory.web_design,
            "Audit found almost no text in the homepage HTML, so this audit may be "
            "incomplete: the page appears to build itself with JavaScript, which this "
            "audit does not run.",
        ),
        FindingSpec(
            "few_reviews",
            Severity.low,
            ServiceCategory.seo,
            "Listing shows {count} {noun}.",
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
    **wording: object,
) -> Finding:
    """Instantiate one catalogue entry. An unknown code is a programming error."""
    spec = CATALOGUE[code]
    return Finding(
        code=spec.code,
        severity=spec.severity,
        service_category=spec.service_category,
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


def for_unreachable(checks: Checks, url: str) -> list[Finding]:
    """A site that did not answer. A certificate failure is reported as such, not as "down"."""
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
    return [
        build(
            "unreachable",
            url=url,
            evidence_text=reachable.evidence_text if reachable is not None else None,
            evidence_url=url,
        )
    ]


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
    if not value_of(checks, "title"):
        findings.append(_from_check(checks, "title", "missing_title"))
    if not value_of(checks, "meta_description"):
        findings.append(_from_check(checks, "meta_description", "missing_meta_description"))
    if value_of(checks, "h1_present") is False:
        findings.append(_from_check(checks, "h1_present", "no_h1"))
    if not value_of(checks, "structured_data"):
        findings.append(_from_check(checks, "structured_data", "no_structured_data"))

    if not any(value_of(checks, key) for key in ("tel_link", "mailto_link", "contact_form")):
        findings.append(_from_check(checks, "contact_form", "no_contact_on_homepage"))

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
    if (
        isinstance(year, int)
        and not isinstance(year, bool)
        and (year <= context.now.year - context.stale_copyright_years)
    ):
        findings.append(_from_check(checks, "copyright_year", "stale_copyright", year=year))
    return findings


QUALITY_SCORES = (
    ("accessibility_score", "low_accessibility_score", "accessibility"),
    ("best_practices_score", "low_best_practices_score", "best practices"),
)


def _quality_score_findings(
    psi: Mapping[str, Any], context: FindingContext, url: str | None
) -> list[Finding]:
    """Lighthouse's category scores, reported below the threshold. A null is never a zero."""
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


def _from_check(checks: Checks, check_key: str, code: str, **wording: object) -> Finding:
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
