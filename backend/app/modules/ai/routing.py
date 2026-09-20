"""Which model handles a business: the cheap one first, the stronger one only when unclear.

The rules are explicit so a reviewer can tell from the stored classification exactly why
a business was escalated. Escalation happens at most once, and only when it is enabled.
"""

from dataclasses import dataclass

from app.modules.ai.schema import AIOutput

# A kept opportunity whose confidence lands here is "unclear": worth a second opinion.
UNCLEAR_LOW = 0.40
UNCLEAR_HIGH = 0.60
JS_SHELL_FINDING = "js_shell_suspected"


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    reasons: tuple[str, ...]


def decide(
    *,
    output: AIOutput | None,
    schema_invalid: bool,
    finding_codes: list[str],
    enabled: bool = True,
) -> EscalationDecision:
    """Whether the triage result calls for the escalation model.

    `output` is the guardrail-cleaned triage answer, or `None` when there is none.
    `schema_invalid` says the triage answer failed the schema after its one retry.
    """
    reasons: list[str] = []
    if schema_invalid:
        reasons.append("schema_invalid_after_retry")
    if JS_SHELL_FINDING in finding_codes:
        reasons.append(JS_SHELL_FINDING)
    if output is not None:
        if any(UNCLEAR_LOW <= o.confidence <= UNCLEAR_HIGH for o in output.opportunities):
            reasons.append("unclear_confidence")
        if not output.industry_matches_listing:
            reasons.append("industry_mismatch")
    return EscalationDecision(escalate=enabled and bool(reasons), reasons=tuple(reasons))
