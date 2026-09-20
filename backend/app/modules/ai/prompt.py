"""Building the input the model sees, and rendering the prompt around it.

The input is *minimised* on purpose (blueprint §6, "Data sent to the AI"): the business's
display name, industry and city/state, its website URL, the audit's finding codes with
their messages and evidence, the PageSpeed score, the tech stack, and the visible page
text cut at `AI_PAGE_TEXT_MAX_CHARS`. Never the phone number, the address, reviewer
notes or anything about a user — and every string that *could* carry a phone number or an
address printed on the page is scrubbed before it goes in.

`ClassificationInput.corpus()` is the exact set of strings a quote may be copied from,
which is what the guardrails check against. Whatever was sent is what may be cited.
"""

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.modules.ai import pii
from app.modules.ai.schema import PROMPT_VERSION
from app.modules.audit_web.models import WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.normalization.taxonomy import OTHER, PLACES_TYPE_TO_INDUSTRY, TEXT_TO_INDUSTRY
from app.modules.opportunities.catalogue import SERVICES, service_keys

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "classify_v1.md"
PAGE_TEXT_OPEN = "<<<PAGE_TEXT"
PAGE_TEXT_CLOSE = "PAGE_TEXT>>>"


def industry_slugs() -> list[str]:
    """Every slug the taxonomy can produce. The only industries the model may name."""
    return sorted({*PLACES_TYPE_TO_INDUSTRY.values(), *TEXT_TO_INDUSTRY.values(), OTHER})


@dataclass(frozen=True)
class InputFinding:
    code: str
    message: str
    evidence_text: str | None
    evidence_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "evidence_text": self.evidence_text,
            "evidence_url": self.evidence_url,
        }


@dataclass(frozen=True)
class ClassificationInput:
    """Exactly what is sent, already scrubbed. Anything not here was never sent."""

    business_name: str
    industry: str | None
    city: str | None
    state: str | None
    website_url: str | None
    page_url: str | None
    findings: list[InputFinding]
    psi_score: int | None
    tech_stack: list[str]
    page_text: str
    page_text_limit: int
    services: list[str] = field(default_factory=service_keys)
    industries: list[str] = field(default_factory=industry_slugs)

    @property
    def finding_codes(self) -> list[str]:
        return [f.code for f in self.findings]

    def corpus(self) -> list[str]:
        """Every string a `quote` may be copied from."""
        texts = [self.page_text]
        for finding in self.findings:
            texts.append(finding.message)
            if finding.evidence_text:
                texts.append(finding.evidence_text)
        return [t for t in texts if t]

    def urls(self) -> set[str]:
        """Every URL in the input; the only ones an evidence item may cite."""
        found = {self.website_url, self.page_url}
        found.update(f.evidence_url for f in self.findings)
        return {u for u in found if u}

    def business_json(self) -> dict[str, Any]:
        return {
            "name": self.business_name,
            "industry_on_listing": self.industry,
            "city": self.city,
            "state": self.state,
            "website": self.website_url,
        }

    def audit_json(self) -> dict[str, Any]:
        return {
            "page_url": self.page_url,
            "findings": [f.as_dict() for f in self.findings],
            "pagespeed_mobile_score": self.psi_score,
            "tech_stack": self.tech_stack,
        }

    def normalised(self) -> str:
        """A canonical serialisation: what the reuse hash is computed over."""
        return json.dumps(
            {
                "business": self.business_json(),
                "audit": self.audit_json(),
                "page_text": self.page_text,
                "services": self.services,
                "industries": self.industries,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def build_input(
    business: Business, audit: WebsiteAudit, *, settings: Settings | None = None
) -> ClassificationInput:
    """Reduce a business and its audit to the minimised, scrubbed input."""
    config = settings or get_settings()
    limit = config.ai_page_text_max_chars
    known = [
        value
        for value in (business.address_line1, business.address_line2, business.postal_code)
        if value
    ]

    def clean(text: str | None) -> str | None:
        if text is None:
            return None
        cleaned = pii.scrub(text, known_addresses=known)
        return cleaned if cleaned else None

    findings = [
        InputFinding(
            code=str(item.get("code")),
            message=str(item.get("message") or ""),
            evidence_text=clean(item.get("evidence_text")),
            evidence_url=item.get("evidence_url"),
        )
        for item in (audit.findings or [])
        if item.get("code")
    ]
    psi_score = (audit.psi or {}).get("performance_score")
    platforms = (audit.tech_stack or {}).get("platforms") or []
    page_text = clean((audit.page_text or "")[:limit]) or ""
    return ClassificationInput(
        business_name=pii.scrub(business.display_name, known_addresses=known),
        industry=business.industry,
        city=business.city,
        state=business.state,
        website_url=business.website,
        page_url=audit.final_url or audit.url_audited or None,
        findings=findings,
        psi_score=int(psi_score) if isinstance(psi_score, int | float) else None,
        tech_stack=[str(p) for p in platforms],
        page_text=page_text,
        page_text_limit=limit,
    )


def input_hash(model: str, classification_input: ClassificationInput) -> str:
    """sha256 over prompt version, model and the canonical input: the reuse key."""
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode())
    digest.update(b"|")
    digest.update(model.encode())
    digest.update(b"|")
    digest.update(classification_input.normalised().encode())
    return digest.hexdigest()


# --- rendering ------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptTemplate:
    version: str
    system: str
    user: str


@lru_cache(maxsize=1)
def load_prompt(path: str | None = None) -> PromptTemplate:
    """Read the checked-in prompt once. The `## System` and `## User` sections are the prompt."""
    text = (Path(path) if path else PROMPT_PATH).read_text(encoding="utf-8")
    _, _, rest = text.partition("## System")
    system, _, user = rest.partition("## User")
    if not system.strip() or not user.strip():
        raise ValueError(f"{PROMPT_PATH} must contain a '## System' and a '## User' section")
    return PromptTemplate(version=PROMPT_VERSION, system=system.strip(), user=user.strip())


def render(classification_input: ClassificationInput) -> tuple[str, str]:
    """The (system, user) pair for one input. Placeholders are replaced, never formatted."""
    template = load_prompt()
    services = {
        key: {"service": spec.name, "typical_findings": list(spec.finding_codes)}
        for key, spec in SERVICES.items()
    }
    replacements = {
        "{{business_json}}": _dump(classification_input.business_json()),
        "{{services_json}}": _dump(services),
        "{{industries_json}}": _dump(classification_input.industries),
        "{{audit_json}}": _dump(classification_input.audit_json()),
        "{{page_text_chars}}": str(len(classification_input.page_text)),
        "{{page_text_limit}}": str(classification_input.page_text_limit),
        # Delimiters inside the page text are neutralised so the page cannot end its own
        # untrusted block early and speak with the prompt's voice.
        "{{page_text}}": _neutralise(classification_input.page_text),
    }
    user = template.user
    for placeholder, value in replacements.items():
        user = user.replace(placeholder, value)
    return template.system, user


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _neutralise(text: str) -> str:
    return text.replace(PAGE_TEXT_OPEN, "<<<PAGE TEXT").replace(PAGE_TEXT_CLOSE, "PAGE TEXT>>>")
