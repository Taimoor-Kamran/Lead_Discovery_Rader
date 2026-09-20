"""What the model is allowed to say, and nothing else.

Two representations of the same contract: a Pydantic model that every answer is parsed
into, and a strict JSON schema derived from it that is sent with every request so the
provider constrains its output to the shape. The Pydantic side is deliberately tolerant
about *values* (a service key that is not in the catalogue still parses) so the guardrails
can reject it with a reason a human reads, rather than a parse error nobody sees.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROMPT_VERSION = "classify-1"
SCHEMA_NAME = "opportunity_classification"
UNKNOWN = "unknown"
SUMMARY_MAX_CHARS = 400
RATIONALE_MAX_CHARS = 300
BuyingIntent = Literal["none_detected", "explicit"]


class AIEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_code: str | None
    quote: str
    source_url: str


class AIOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence: list[AIEvidence]


class AIOutput(BaseModel):
    """The validated answer. Post-validation guardrails run on top of this, always."""

    model_config = ConfigDict(extra="forbid")

    business_summary: str
    industry: str
    industry_matches_listing: bool
    opportunities: list[AIOpportunity]
    buying_intent: BuyingIntent
    unknowns: list[str]
    needs_human_review: bool

    @classmethod
    def unknown(cls) -> "AIOutput":
        """The answer that says nothing: what the fake provider gives an unknown input."""
        return cls(
            business_summary=UNKNOWN,
            industry=UNKNOWN,
            industry_matches_listing=True,
            opportunities=[],
            buying_intent="none_detected",
            unknowns=["No fixture answer exists for this business"],
            needs_human_review=True,
        )


class SchemaDriftError(Exception):
    """The model's text is not the schema. Retried once, then escalated, then given up."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_output(text: str) -> AIOutput:
    """Turn raw model text into the validated shape, or say precisely why it is not."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SchemaDriftError(f"not JSON: {type(exc).__name__}") from exc
    try:
        return AIOutput.model_validate(payload)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        raise SchemaDriftError(f"schema mismatch: {errors}") from exc


def json_schema(service_keys: list[str]) -> dict[str, Any]:
    """The strict schema sent to the provider.

    OpenAI's strict mode wants every property required and `additionalProperties: false`
    on every object, which Pydantic's export does not guarantee, so the schema is written
    out by hand here and a test proves it matches the Pydantic model field for field.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "business_summary": {
                "type": "string",
                "description": (
                    f"At most {SUMMARY_MAX_CHARS} characters, from the page text only, "
                    f"or '{UNKNOWN}'."
                ),
            },
            "industry": {
                "type": "string",
                "description": f"One industry slug from the list given, or '{UNKNOWN}'.",
            },
            "industry_matches_listing": {"type": "boolean"},
            "opportunities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "service": {"type": "string", "enum": list(service_keys)},
                        "confidence": {"type": "number"},
                        "rationale": {
                            "type": "string",
                            "description": (
                                f"At most {RATIONALE_MAX_CHARS} characters, neutral wording."
                            ),
                        },
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "finding_code": {"type": ["string", "null"]},
                                    "quote": {
                                        "type": "string",
                                        "description": "Copied verbatim from the input.",
                                    },
                                    "source_url": {"type": "string"},
                                },
                                "required": ["finding_code", "quote", "source_url"],
                            },
                        },
                    },
                    "required": ["service", "confidence", "rationale", "evidence"],
                },
            },
            "buying_intent": {"type": "string", "enum": ["none_detected", "explicit"]},
            "unknowns": {"type": "array", "items": {"type": "string"}},
            "needs_human_review": {"type": "boolean"},
        },
        "required": [
            "business_summary",
            "industry",
            "industry_matches_listing",
            "opportunities",
            "buying_intent",
            "unknowns",
            "needs_human_review",
        ],
    }
