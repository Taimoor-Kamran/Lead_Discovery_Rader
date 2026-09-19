"""Post-validation of every AI answer. The model is never trusted (blueprint slide 35).

Each rule below is one row of the spec's guardrail table, and each has a unit test of its
own. What survives is an `AIOutput` in which every quote is a verbatim substring of the
input that was sent, every finding code is one the audit actually produced, every service
is in the catalogue, nothing personal appears, no forbidden wording reaches a human, and
buying intent is `explicit` only when a valid quote says so in the configured words.

What was thrown away is not lost: `rejected_claims` records every drop with a reason, and
is stored on the classification so a reviewer can see what the model tried to say.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.modules.ai import pii
from app.modules.ai.schema import UNKNOWN, AIEvidence, AIOpportunity, AIOutput
from app.modules.audit_web.findings import BANNED_WORDS
from app.modules.opportunities.catalogue import is_service

_WS = re.compile(r"\s+")
# Curly quotes and dashes a model tends to "tidy" into ASCII, folded both ways so the
# verbatim rule is about words, not typography.
_TYPOGRAPHY = {
    0x2018: "'",  # left single quotation mark
    0x2019: "'",  # right single quotation mark
    0x201C: '"',  # left double quotation mark
    0x201D: '"',  # right double quotation mark
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
}


def normalise(text: str) -> str:
    """Whitespace collapsed, typography folded, case folded: the verbatim comparison key."""
    return _WS.sub(" ", text.translate(_TYPOGRAPHY)).strip().casefold()


@dataclass(frozen=True)
class GuardrailContext:
    """Everything the rules may consult: only what was sent, and what is configured."""

    corpus: list[str]
    finding_codes: frozenset[str]
    urls: frozenset[str]
    industries: frozenset[str]
    intent_patterns: tuple[str, ...]
    page_url: str | None = None

    @classmethod
    def build(
        cls,
        *,
        corpus: Iterable[str],
        finding_codes: Iterable[str],
        urls: Iterable[str],
        industries: Iterable[str],
        intent_patterns: Iterable[str],
        page_url: str | None = None,
    ) -> "GuardrailContext":
        return cls(
            corpus=[normalise(t) for t in corpus if t],
            finding_codes=frozenset(finding_codes),
            urls=frozenset(urls),
            industries=frozenset(industries),
            intent_patterns=tuple(normalise(p) for p in intent_patterns if p.strip()),
            page_url=page_url,
        )

    def contains(self, quote: str) -> bool:
        needle = normalise(quote)
        return bool(needle) and any(needle in text for text in self.corpus)


@dataclass
class GuardrailResult:
    output: AIOutput
    rejected_claims: list[dict[str, Any]] = field(default_factory=list)

    @property
    def trimmed(self) -> bool:
        return bool(self.rejected_claims)


def apply(output: AIOutput, context: GuardrailContext) -> GuardrailResult:
    """Run every rule. Returns a new output; the model's original is never mutated."""
    rejected: list[dict[str, Any]] = []
    opportunities = [
        kept
        for opportunity in output.opportunities
        if (kept := _check_opportunity(opportunity, context, rejected)) is not None
    ]

    summary = _scrub_text(output.business_summary, context, rejected, where="business_summary")
    industry = output.industry
    if industry != UNKNOWN and industry not in context.industries:
        rejected.append(_claim("unknown_industry", detail=f"'{industry}' is not a taxonomy slug"))
        industry = UNKNOWN

    intent = output.buying_intent
    if intent == "explicit":
        matched = _intent_quote(opportunities, context)
        if matched is None:
            rejected.append(
                _claim(
                    "intent_without_evidence",
                    detail="no valid evidence quote matches the explicit-intent patterns",
                )
            )
            intent = "none_detected"

    cleaned = AIOutput(
        business_summary=summary,
        industry=industry,
        industry_matches_listing=output.industry_matches_listing,
        opportunities=opportunities,
        buying_intent=intent,
        unknowns=list(output.unknowns),
        # Always. The model does not get to decide that a human is not needed.
        needs_human_review=True,
    )
    return GuardrailResult(output=cleaned, rejected_claims=rejected)


def _check_opportunity(
    opportunity: AIOpportunity, context: GuardrailContext, rejected: list[dict[str, Any]]
) -> AIOpportunity | None:
    service = opportunity.service
    if not is_service(service):
        rejected.append(
            _claim("unknown_service", service=service, detail="not in the service catalogue")
        )
        return None

    evidence: list[AIEvidence] = []
    for item in opportunity.evidence:
        checked = _check_evidence(item, service, context, rejected)
        if checked is not None:
            evidence.append(checked)
    if not evidence:
        rejected.append(
            _claim("no_valid_evidence", service=service, detail="every evidence item was dropped")
        )
        return None

    rationale = _scrub_text(
        opportunity.rationale, context, rejected, where="rationale", service=service
    )
    if rationale and _banned_word(rationale) is not None:
        rejected.append(
            _claim(
                "wording_in_rationale",
                service=service,
                detail=f"rationale contains '{_banned_word(rationale)}'",
                text=opportunity.rationale,
            )
        )
        rationale = ""

    return AIOpportunity(
        service=service,
        confidence=opportunity.confidence,
        rationale=rationale,
        evidence=evidence,
    )


def _check_evidence(
    item: AIEvidence,
    service: str,
    context: GuardrailContext,
    rejected: list[dict[str, Any]],
) -> AIEvidence | None:
    if item.finding_code is not None and item.finding_code not in context.finding_codes:
        rejected.append(
            _claim(
                "unknown_finding_code",
                service=service,
                detail=f"'{item.finding_code}' is not a finding of this audit",
                text=item.quote,
            )
        )
        return None
    if not context.contains(item.quote):
        rejected.append(
            _claim(
                "quote_not_in_input",
                service=service,
                detail="quote is not a substring of the input that was sent",
                text=item.quote,
            )
        )
        return None
    source_url = item.source_url
    if source_url not in context.urls:
        replacement = context.page_url or next(iter(sorted(context.urls)), "")
        rejected.append(
            _claim(
                "url_not_in_input",
                service=service,
                detail=f"'{source_url}' was not in the input; replaced with the page URL",
            )
        )
        source_url = replacement
    return AIEvidence(finding_code=item.finding_code, quote=item.quote, source_url=source_url)


def _scrub_text(
    text: str,
    context: GuardrailContext,
    rejected: list[dict[str, Any]],
    *,
    where: str,
    service: str | None = None,
) -> str:
    """Blank a field that contains an email, a phone number or a URL that was never sent."""
    problems = pii.find_emails(text) + pii.find_phones(text)
    problems += [url for url in pii.find_urls(text) if _strip_url(url) not in context.urls]
    if not problems:
        return text
    rejected.append(
        _claim(
            f"pii_in_{where}",
            service=service,
            detail=f"{where} contains contact details or a URL not in the input; blanked",
            text=text,
        )
    )
    return ""


def _strip_url(url: str) -> str:
    return url.rstrip(".,;:")


def _banned_word(text: str) -> str | None:
    lowered = text.lower()
    for word in BANNED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return word
    return None


def _intent_quote(opportunities: list[AIOpportunity], context: GuardrailContext) -> str | None:
    """The first valid quote that matches an explicit-intent pattern, if any."""
    for opportunity in opportunities:
        for item in opportunity.evidence:
            quote = normalise(item.quote)
            if any(pattern in quote for pattern in context.intent_patterns):
                return item.quote
    return None


def _claim(rule: str, **extra: Any) -> dict[str, Any]:
    claim: dict[str, Any] = {"rule": rule}
    claim.update({k: v for k, v in extra.items() if v is not None})
    return claim
