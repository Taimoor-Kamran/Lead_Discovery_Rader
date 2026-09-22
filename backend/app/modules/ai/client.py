"""The one shape every LLM provider is reduced to.

The classification service never imports a provider. It builds an `LLMRequest`, hands it
to whatever `LLMClient` the environment wired up, and gets back an `LLMResult` carrying
the text plus what the call cost in tokens and time. That is what lets the same code run
against OpenAI in production, the fake provider in development and the tests, and a
future provider without touching the service.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Tier = Literal["triage", "escalation"]


@dataclass(frozen=True)
class LLMRequest:
    """One completion request: a system prompt, a user prompt and the schema to obey."""

    model: str
    tier: Tier
    system: str
    user: str
    schema_name: str
    json_schema: Mapping[str, Any]
    # A stable handle for the business being classified. The fake provider keys its
    # fixtures on it; the real provider ignores it.
    fixture_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    """What came back, and what it cost. `text` is the raw model output, unparsed."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class LLMError(Exception):
    """The provider did not answer usefully. `retryable` says whether trying again could help.

    `latency_ms` is how long the failed call took. A failure costs real time — it is a
    timeout, or a round trip that ended in a 400 — and recording it as 0 makes a run that
    spent a minute failing look instant (spec v0.9.0, the production 400s).
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        latency_ms: int = 0,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.latency_ms = latency_ms


class LLMClient(Protocol):
    provider: str

    def complete(self, request: LLMRequest) -> LLMResult: ...
