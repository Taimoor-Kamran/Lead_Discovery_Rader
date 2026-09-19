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
            "no_contact_on_homepage",
            Severity.high,
            ServiceCategory.web_design,
            "Audit found no phone link, email link or contact form on the homepage.",
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
            "js_shell_suspected",
            Severity.info,
            ServiceCategory.web_design,
            "Audit found almost no text in the homepage HTML, so this audit may be "
            "incomplete: the page appears to build itself with JavaScript, which this "
            "audit does not run.",
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

    if value_of(checks, "js_shell_suspected"):
        findings.append(_from_check(checks, "js_shell_suspected", "js_shell_suspected"))
    return findings


def _content_findings(checks: Checks, context: FindingContext) -> list[Finding]:
    findings: list[Finding] = []
    if value_of(checks, "viewport_meta") is None:
        findings.append(_from_check(checks, "viewport_meta", "no_mobile_viewport"))
    if not value_of(checks, "title"):
        findings.append(_from_check(checks, "title", "missing_title"))
    if not value_of(checks, "meta_description"):
        findings.append(_from_check(checks, "meta_description", "missing_meta_description"))
    if value_of(checks, "h1_present") is False:
        findings.append(_from_check(checks, "h1_present", "no_h1"))
    if value_of(checks, "structured_data") is None:
        findings.append(_from_check(checks, "structured_data", "no_structured_data"))

    if not any(value_of(checks, key) for key in ("tel_link", "mailto_link", "contact_form")):
        findings.append(_from_check(checks, "contact_form", "no_contact_on_homepage"))

    industry = (context.industry or "").lower()
    if value_of(checks, "booking") is None and industry in context.booking_industries:
        findings.append(_from_check(checks, "booking", "no_online_booking"))

    year = value_of(checks, "copyright_year")
    if isinstance(year, int) and year <= context.now.year - context.stale_copyright_years:
        findings.append(_from_check(checks, "copyright_year", "stale_copyright", year=year))
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
