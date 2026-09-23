"""Deterministic opportunities: audit findings → services. Always runs, needs no AI.

A rule opportunity is a restatement of the audit in service terms. Its reason is built
from the findings' own messages (which already pass the wording rule), and its evidence is
the findings' evidence, copied verbatim. It claims nothing the audit did not.
"""

from dataclasses import dataclass, field
from typing import Any

from app.modules.audit_web.findings import Severity
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import BusinessStatus
from app.modules.opportunities.catalogue import (
    ADS_SOCIAL,
    ADS_SOCIAL_CONFIDENCE,
    NO_OPPORTUNITY_FINDINGS,
    SERVICES,
    SEVERITY_CONFIDENCE,
    combine,
    service_for_finding,
)

ADS_SOCIAL_REASON = "Audit found no social profile links on the homepage."
# The only findings an unreachable site may contribute: the site itself could not be read,
# so nothing about its content is known.
# `few_reviews` is read from the listing, not the site, so it stands however the site did.
UNREACHABLE_FINDINGS = frozenset({"unreachable", "tls_invalid", "few_reviews"})


@dataclass(frozen=True)
class Evidence:
    finding_code: str | None
    text: str
    url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"finding_code": self.finding_code, "text": self.text, "url": self.url}


@dataclass
class RuleOpportunity:
    service: str
    confidence: float
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    finding_codes: list[str] = field(default_factory=list)


def rule_opportunities(business: Business, audit: WebsiteAudit) -> list[RuleOpportunity]:
    """Every service the audit points at, in catalogue order, with its combined confidence."""
    if business.business_status is BusinessStatus.closed_permanently:
        return []
    if audit.status is AuditStatus.robots_blocked or audit.status is AuditStatus.failed:
        return []
    codes = set(audit.finding_codes)
    if codes & NO_OPPORTUNITY_FINDINGS:
        return []

    findings = [item for item in (audit.findings or []) if item.get("code")]
    if audit.status is AuditStatus.unreachable:
        findings = [item for item in findings if item["code"] in UNREACHABLE_FINDINGS]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in findings:
        service = service_for_finding(str(item["code"]))
        if service is not None:
            grouped.setdefault(service, []).append(item)

    result: list[RuleOpportunity] = []
    for service in SERVICES:
        items = grouped.get(service)
        if not items:
            continue
        result.append(
            RuleOpportunity(
                service=service,
                confidence=combine([_confidence(item) for item in items]),
                reason=" ".join(str(item.get("message") or "").strip() for item in items).strip(),
                evidence=[
                    Evidence(
                        finding_code=str(item["code"]),
                        text=str(item.get("evidence_text") or item.get("message") or ""),
                        url=item.get("evidence_url"),
                    )
                    for item in items
                ],
                finding_codes=[str(item["code"]) for item in items],
            )
        )

    social = _ads_social(audit)
    if social is not None:
        result.append(social)
    return result


def _confidence(finding: dict[str, Any]) -> float:
    try:
        severity = Severity(str(finding.get("severity")))
    except ValueError:
        severity = Severity.info
    return SEVERITY_CONFIDENCE[severity]


def _ads_social(audit: WebsiteAudit) -> RuleOpportunity | None:
    """The one opportunity that comes from a check rather than a finding."""
    if audit.status is not AuditStatus.done:
        return None
    checks = audit.checks or {}
    if not (checks.get("parsed") or {}).get("value"):
        return None
    social = checks.get("social_links")
    if social is None or social.get("value") != []:
        return None
    return RuleOpportunity(
        service=ADS_SOCIAL,
        confidence=ADS_SOCIAL_CONFIDENCE,
        reason=ADS_SOCIAL_REASON,
        evidence=[
            Evidence(
                finding_code=None,
                text=str(social.get("evidence_text") or ADS_SOCIAL_REASON),
                url=social.get("evidence_url") or audit.final_url or audit.url_audited,
            )
        ],
        finding_codes=[],
    )
