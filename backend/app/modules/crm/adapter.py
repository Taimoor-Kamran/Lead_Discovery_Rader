"""The CRM adapter contract, its errors, and the registry of destinations.

An adapter knows how to talk to one CRM. It never decides *what* may be sent: the service
layer applies the human gate before every call, so an adapter only ever sees a record for
a business with at least one approved opportunity and no active suppression.

Errors say whether retrying can help. `CrmTransientError` (a timeout, a 429, a 5xx) is
retried with backoff; the other three hold the lead until a person looks at it, because a
bad token, a missing column or a rejected payload will fail the same way tomorrow.
"""

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError

CrmAction = Literal["created", "updated", "unchanged"]


class Destination(enum.StrEnum):
    csv = "csv"
    airtable = "airtable"
    fake = "fake"


# --- errors -------------------------------------------------------------------------------


class CrmError(AppError):
    """The CRM could not be used. Not retryable unless a subclass says otherwise."""

    status_code = 502
    code = "crm_error"
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, details=details, code=code)
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class CrmTransientError(CrmError):
    """A timeout, a connection failure, a 429 or a 5xx. Worth another try."""

    status_code = 503
    code = "crm_unavailable"
    retryable: ClassVar[bool] = True


class CrmAuthError(CrmError):
    """401/403: the token is wrong, expired or lacks a scope. Retrying cannot fix it."""

    code = "crm_auth_failed"


class CrmConfigError(CrmError):
    """The destination is not set up: no token, no base, a missing field, a bad table."""

    code = "crm_config_invalid"


class CrmRejectedError(CrmError):
    """The CRM refused the payload (a 400/422 with a message). A person must look."""

    code = "crm_rejected"


# --- records and results ------------------------------------------------------------------


@dataclass(frozen=True)
class CrmRecord:
    """One business as the CRM should see it: canonical field keys → plain values.

    Keys are the `fields.py` keys; adapters map them onto their own column names. Values
    are text, numbers, booleans, ISO dates or `None` — never HTML.
    """

    business_id: uuid.UUID
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def radar_business_id(self) -> str:
        return str(self.business_id)


@dataclass(frozen=True)
class CrmResult:
    external_id: str
    action: CrmAction
    external_url: str | None = None


@dataclass(frozen=True)
class CrmCheck:
    """One line of `check()`: what was looked at, whether it is fine, and a detail."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class CrmHealth:
    destination: str
    ok: bool
    checks: list[CrmCheck] = field(default_factory=list)
    message: str | None = None


class CrmAdapter(Protocol):
    """What every destination implements. See the module docstring for the guarantees."""

    name: str

    def check(self) -> CrmHealth: ...

    def upsert(self, record: CrmRecord, existing_external_id: str | None) -> CrmResult:
        """Create the record, or update the Radar-owned fields of an existing one.

        On an update the adapter must send **only** the Radar-owned fields: a salesperson's
        Assigned rep, Status, Follow-up date and Notes are theirs.
        """
        ...

    def find_by_keys(
        self, radar_business_id: str, domain: str | None, phone: str | None
    ) -> str | None:
        """An existing record for this business, if the CRM already has one. Never creates."""
        ...

    def mark_do_not_contact(self, external_id: str, note: str, *, flag: bool = True) -> CrmResult:
        """Set (or, when lifted, clear) the Do not contact field. Never deletes the record."""
        ...

    def withdraw(self, external_id: str) -> CrmResult:
        """Every approval was undone after the sync: set Status to Withdrawn, but only while
        it still reads `New`. A status a salesperson changed is left alone."""
        ...


# --- registry -----------------------------------------------------------------------------

AdapterFactory = Callable[[Session, Settings], CrmAdapter]

_FACTORIES: dict[str, AdapterFactory] = {}
_bootstrapped = False


def _ensure_builtins() -> None:
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    from app.modules.crm.airtable_adapter import build_airtable_adapter
    from app.modules.crm.csv_adapter import build_csv_adapter
    from app.modules.crm.fake_adapter import build_fake_adapter

    _FACTORIES.setdefault(Destination.csv.value, build_csv_adapter)
    _FACTORIES.setdefault(Destination.airtable.value, build_airtable_adapter)
    _FACTORIES.setdefault(Destination.fake.value, build_fake_adapter)


def register(name: str, factory: AdapterFactory, *, replace: bool = False) -> None:
    """Add a destination. A duplicate name is a programming error unless `replace` is set."""
    _ensure_builtins()
    if not name:
        raise ValueError("A CRM adapter must have a non-empty name")
    if name in _FACTORIES and not replace:
        raise ValueError(f"A CRM adapter named '{name}' is already registered")
    _FACTORIES[name] = factory


def unregister(name: str) -> AdapterFactory | None:
    return _FACTORIES.pop(name, None)


def names() -> list[str]:
    _ensure_builtins()
    return sorted(_FACTORIES)


def snapshot() -> dict[str, AdapterFactory]:
    """A copy of the registry, so a test can put back what it replaced."""
    _ensure_builtins()
    return dict(_FACTORIES)


def restore(saved: dict[str, AdapterFactory]) -> None:
    _FACTORIES.clear()
    _FACTORIES.update(saved)


def build(
    session: Session, name: str | None = None, *, settings: Settings | None = None
) -> CrmAdapter:
    """The adapter for a destination (the configured one when `name` is not given)."""
    _ensure_builtins()
    config = settings or get_settings()
    wanted = name or config.crm_destination
    try:
        factory = _FACTORIES[wanted]
    except KeyError as exc:
        raise CrmConfigError(
            f"No CRM adapter is registered for destination '{wanted}'",
            details={"destination": wanted, "known": sorted(_FACTORIES)},
        ) from exc
    return factory(session, config)
