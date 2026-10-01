"""Which finding codes are domain evidence, and whether an audit's lookup for one worked.

A domain finding is judged by the DNS or RDAP question it comes from, never by the
homepage fetch. So "this audit no longer shows the finding" means the finding is gone only
when that question was actually answered in this audit: an `unknown` DNS answer, a failed
RDAP call, or a skipped lookup says nothing about the domain (spec v0.13.0, amendment W).
"""

from typing import Any

from app.core.dns import Outcome
from app.modules.domain_intel import dns_lookup

CHECK_KEY = "domain_intel"

RDAP_FINDINGS = frozenset({"domain_expired", "domain_expiring_soon"})
DNS_FINDINGS = frozenset(
    {
        "domain_no_a_record",
        "multiple_spf_records",
        "spf_allows_all",
        "no_spf",
        "no_dmarc",
        "dmarc_policy_none",
        "no_domain_mx",
    }
)
DOMAIN_FINDINGS = RDAP_FINDINGS | DNS_FINDINGS


def lookup_worked(checks: dict[str, Any] | None, code: str) -> bool:
    """True when the audit's stored snapshot answered the question behind `code`."""
    stored = (checks or {}).get(CHECK_KEY)
    value = stored.get("value") if isinstance(stored, dict) else None
    if not isinstance(value, dict):
        return False
    if code in RDAP_FINDINGS:
        return isinstance(value.get("rdap"), dict)
    snapshot = value.get("dns")
    if code not in DNS_FINDINGS or not isinstance(snapshot, dict):
        return False
    return all(
        dns_lookup.state(snapshot, name, rdtype) is not Outcome.unknown
        for name, rdtype in _questions(snapshot, code)
    )


def _questions(snapshot: dict[str, Any], code: str) -> list[tuple[str, str]]:
    apex = str(snapshot.get("apex") or "")
    if code == "domain_no_a_record":
        host = str(snapshot.get("site_host") or "")
        return [(host, rdtype) for rdtype in dns_lookup.SITE_TYPES]
    if code in {"no_dmarc", "dmarc_policy_none"}:
        return [(dns_lookup.dmarc_name(apex), "TXT")]
    if code == "no_domain_mx":
        return [(apex, "MX")]
    return [(apex, "TXT")]
