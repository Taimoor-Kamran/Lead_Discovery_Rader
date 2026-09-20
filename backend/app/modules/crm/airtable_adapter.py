"""Airtable, through `core/http.py`: metered in `api_calls`, rate limited, token never logged.

Confirmed against Airtable's REST API docs while writing this: 5 requests per second per
base (a 429 asks for a 30-second wait), `GET /v0/meta/bases/{base}/tables` under
`schema.bases:read`, `POST` the same path under `schema.bases:write` to create a table,
`performUpsert.fieldsToMergeOn` on `PATCH /v0/{base}/{table}`, and at most
`RECORDS_PER_REQUEST` records per create/update request.

The upsert option is deliberately **not** used: it merges a whole record onto whatever
already carries the same Radar Business ID, CRM-owned fields included. Finding first and
then creating or PATCHing only the Radar-owned fields is what keeps a salesperson's Status
and Notes theirs.

The HTTP client makes exactly one attempt per call. Retries are the sync service's job,
so every one of them is a `crm_sync_attempts` row a CRM manager can see.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.http import ApiHttpClient
from app.core.logging import get_logger
from app.core.ratelimit import build_limiter
from app.modules.adapters.errors import (
    AdapterError,
    AuthError,
    QuotaExceededError,
    RateLimitedError,
    SchemaError,
    TransientError,
)
from app.modules.crm.adapter import (
    CrmAuthError,
    CrmCheck,
    CrmConfigError,
    CrmError,
    CrmHealth,
    CrmRecord,
    CrmRejectedError,
    CrmResult,
    CrmTransientError,
    Destination,
)
from app.modules.crm.fields import (
    FIELDS,
    STATUS_NEW,
    STATUS_WITHDRAWN,
    UPSERT_KEY,
    CrmField,
    Kind,
    radar_owned,
)
from app.modules.sources.models import SourceKind

logger = get_logger("app.crm.airtable")

AIRTABLE_SOURCE_NAME = "airtable"
# Marks the `sources` row: an API the pipeline writes leads to, never a source to search.
CRM_DESTINATION_ROLE = "crm_destination"
API_BASE = "https://api.airtable.com/v0"
META_PATH = "meta/bases"
# Airtable's documented cap on records per create/update request.
RECORDS_PER_REQUEST = 10
# What Airtable documents; the default AIRTABLE_RPS of 4 stays under it.
AIRTABLE_DOCUMENTED_RPS = 5
DEFAULT_FIELD_MAP_PATH = Path(__file__).with_name("crm_field_map.airtable.json")
TERMS_URL = "https://www.airtable.com/company/tos"

# Which Airtable column types can hold each kind of value (for `crm-check`).
COMPATIBLE_TYPES: dict[Kind, frozenset[str]] = {
    Kind.text: frozenset(
        {
            "singleLineText",
            "multilineText",
            "richText",
            "singleSelect",
            "email",
            "url",
            "phoneNumber",
        }
    ),
    Kind.long_text: frozenset({"multilineText", "richText", "singleLineText"}),
    Kind.number: frozenset({"number"}),
    Kind.checkbox: frozenset({"checkbox"}),
    Kind.date: frozenset({"date", "dateTime"}),
    Kind.url: frozenset({"url", "singleLineText", "multilineText"}),
}

# The column each field gets when `make crm-bootstrap-airtable` creates the table.
BOOTSTRAP_TYPES: dict[Kind, dict[str, Any]] = {
    Kind.text: {"type": "singleLineText"},
    Kind.long_text: {"type": "multilineText"},
    Kind.number: {"type": "number", "options": {"precision": 0}},
    Kind.checkbox: {"type": "checkbox", "options": {"color": "redBright", "icon": "xCheckbox"}},
    Kind.date: {"type": "date", "options": {"dateFormat": {"name": "iso"}}},
    Kind.url: {"type": "url"},
}


def load_field_map(path: str | Path | None = None) -> dict[str, str]:
    """Radar key → Airtable column name. Every field must be mapped; a gap is a config error."""
    target = Path(path) if path else DEFAULT_FIELD_MAP_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CrmConfigError(
            f"Could not read the Airtable field map at {target}", details={"path": str(target)}
        ) from exc
    mapping = {
        key: str(value).strip()
        for key, value in raw.items()
        if not key.startswith("_") and isinstance(value, str) and value.strip()
    }
    missing = [item.key for item in FIELDS if item.key not in mapping]
    if missing:
        raise CrmConfigError(
            "The Airtable field map does not name a column for every field",
            details={"path": str(target), "missing": missing},
        )
    return mapping


def _formula_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class AirtableAdapter:
    name = Destination.airtable.value

    def __init__(
        self,
        *,
        http: ApiHttpClient,
        token: str,
        base_id: str,
        table: str,
        field_map: dict[str, str],
    ) -> None:
        self._http = http
        self._token = token
        self._base_id = base_id.strip()
        self._table = table.strip()
        self._map = field_map
        self._table_id: str | None = table.strip() if table.strip().startswith("tbl") else None

    # --- the contract ---------------------------------------------------------------------

    def check(self) -> CrmHealth:
        checks: list[CrmCheck] = []
        try:
            self._require_config()
        except CrmConfigError as exc:
            return CrmHealth(
                self.name, False, [CrmCheck("config", False, exc.message)], exc.message
            )
        checks.append(CrmCheck("config", True, "AIRTABLE_TOKEN and AIRTABLE_BASE_ID are set"))
        try:
            table = self._table_schema()
        except CrmError as exc:
            checks.append(CrmCheck("base", False, exc.message))
            return CrmHealth(self.name, False, checks, exc.message)
        if table is None:
            detail = f"table '{self._table}' not found in base {self._base_id}"
            checks.append(CrmCheck("table", False, detail))
            return CrmHealth(self.name, False, checks, detail)
        checks.append(CrmCheck("table", True, f"table '{table.get('name')}' ({table.get('id')})"))
        by_name = {
            str(item.get("name")): str(item.get("type"))
            for item in table.get("fields") or []
            if isinstance(item, dict)
        }
        for item in FIELDS:
            label = self._map[item.key]
            actual = by_name.get(label)
            if actual is None:
                checks.append(CrmCheck(label, False, f"missing (expected {self._expected(item)})"))
            elif actual not in COMPATIBLE_TYPES[item.kind]:
                checks.append(
                    CrmCheck(label, False, f"type '{actual}' cannot hold {item.kind.value} values")
                )
            else:
                checks.append(CrmCheck(label, True, actual))
        ok = all(check.ok for check in checks)
        return CrmHealth(
            self.name,
            ok,
            checks,
            "Airtable is reachable and every field exists"
            if ok
            else "Some Airtable fields are missing or of the wrong type; see checks",
        )

    def upsert(self, record: CrmRecord, existing_external_id: str | None) -> CrmResult:
        self._require_config()
        if existing_external_id is not None:
            payload = {"fields": self._labeled(radar_owned(record.fields)), "typecast": True}
            self._call(
                "PATCH",
                f"{self._records_url()}/{quote(existing_external_id, safe='')}",
                json=payload,
            )
            return CrmResult(
                existing_external_id, "updated", self.external_url(existing_external_id)
            )
        fields = {k: v for k, v in self._labeled(record.fields).items() if v is not None}
        body = self._call(
            "POST", self._records_url(), json={"records": [{"fields": fields}], "typecast": True}
        )
        records = body.get("records") if isinstance(body, dict) else None
        if not records or not isinstance(records[0], dict) or not records[0].get("id"):
            raise CrmRejectedError("Airtable created no record", details={"body": str(body)[:500]})
        record_id = str(records[0]["id"])
        return CrmResult(record_id, "created", self.external_url(record_id))

    def find_by_keys(
        self, radar_business_id: str, domain: str | None, phone: str | None
    ) -> str | None:
        self._require_config()
        formulas = [f"{{{self._map[UPSERT_KEY]}}}={_formula_string(radar_business_id)}"]
        if domain:
            formulas.append(
                f"FIND({_formula_string(domain.lower())}, LOWER({{{self._map['website']}}}))"
            )
        if phone:
            formulas.append(f"{{{self._map['public_phone']}}}={_formula_string(phone)}")
        for formula in formulas:
            query = urlencode(
                {"filterByFormula": formula, "maxRecords": 1, "fields[]": self._map[UPSERT_KEY]}
            )
            body = self._call("GET", f"{self._records_url()}?{query}")
            records = body.get("records") if isinstance(body, dict) else None
            if records and isinstance(records[0], dict) and records[0].get("id"):
                return str(records[0]["id"])
        return None

    def mark_do_not_contact(self, external_id: str, note: str, *, flag: bool = True) -> CrmResult:
        self._require_config()
        payload = {"fields": {self._map["do_not_contact"]: bool(flag)}, "typecast": True}
        self._call("PATCH", f"{self._records_url()}/{quote(external_id, safe='')}", json=payload)
        return CrmResult(external_id, "updated", self.external_url(external_id))

    def withdraw(self, external_id: str) -> CrmResult:
        self._require_config()
        url = f"{self._records_url()}/{quote(external_id, safe='')}"
        current = self._call("GET", url)
        fields = current.get("fields") if isinstance(current, dict) else None
        status = (fields or {}).get(self._map["status"]) if isinstance(fields, dict) else None
        if status != STATUS_NEW:
            return CrmResult(external_id, "unchanged", self.external_url(external_id))
        self._call(
            "PATCH", url, json={"fields": {self._map["status"]: STATUS_WITHDRAWN}, "typecast": True}
        )
        return CrmResult(external_id, "updated", self.external_url(external_id))

    # --- extras ------------------------------------------------------------------------------

    def external_url(self, record_id: str) -> str | None:
        """A link to the record, when the table id is known. Never fails a sync over it."""
        if self._table_id is None:
            try:
                table = self._table_schema()
            except CrmError:
                return None
            if table is None or not table.get("id"):
                return None
            self._table_id = str(table["id"])
        return f"https://airtable.com/{self._base_id}/{self._table_id}/{record_id}"

    def bootstrap_table(self) -> str:
        """Create the Leads table with every field. Refuses when the table already exists."""
        self._require_config()
        if self._table_schema() is not None:
            raise CrmConfigError(
                f"Table '{self._table}' already exists in base {self._base_id}; "
                "refusing to touch it",
                details={"table": self._table},
            )
        fields = [{"name": self._map[item.key], **BOOTSTRAP_TYPES[item.kind]} for item in FIELDS]
        body = self._call(
            "POST",
            f"{API_BASE}/{META_PATH}/{quote(self._base_id, safe='')}/tables",
            json={
                "name": self._table,
                "description": "Leads approved in Lead Discovery Radar. Radar keeps the "
                "business fields up to date; Assigned rep, Status, Follow-up date and Notes "
                "are yours.",
                "fields": fields,
            },
        )
        table_id = str(body.get("id")) if isinstance(body, dict) and body.get("id") else ""
        if not table_id:
            raise CrmRejectedError("Airtable created no table", details={"body": str(body)[:500]})
        self._table_id = table_id
        return table_id

    # --- plumbing ----------------------------------------------------------------------------

    def _require_config(self) -> None:
        if not self._token:
            raise CrmConfigError("AIRTABLE_TOKEN is not set")
        if not self._base_id:
            raise CrmConfigError("AIRTABLE_BASE_ID is not set")
        if not self._table:
            raise CrmConfigError("AIRTABLE_TABLE is not set")

    def _expected(self, item: CrmField) -> str:
        return str(BOOTSTRAP_TYPES[item.kind]["type"])

    def _labeled(self, values: dict[str, Any]) -> dict[str, Any]:
        return {self._map[key]: value for key, value in values.items() if key in self._map}

    def _records_url(self) -> str:
        return f"{API_BASE}/{quote(self._base_id, safe='')}/{quote(self._table, safe='')}"

    def _table_schema(self) -> dict[str, Any] | None:
        body = self._call("GET", f"{API_BASE}/{META_PATH}/{quote(self._base_id, safe='')}/tables")
        tables = body.get("tables") if isinstance(body, dict) else None
        for table in tables or []:
            if isinstance(table, dict) and self._table in (table.get("name"), table.get("id")):
                return table
        return None

    def _call(self, method: str, url: str, *, json: Any = None) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            return self._http.request_json(
                method, url, parse=lambda body: body, headers=headers, json=json
            )
        except AuthError as exc:
            raise CrmAuthError(
                "Airtable rejected the token. Check AIRTABLE_TOKEN, that it has "
                "data.records:read/write for this base, and that the base id is right.",
                details=exc.details,
                http_status=_status(exc),
            ) from exc
        except RateLimitedError as exc:
            retry_after = exc.details.get("retry_after_seconds")
            raise CrmTransientError(
                "Airtable is rate limiting this client",
                details=exc.details,
                http_status=429,
                retry_after_seconds=float(retry_after) if retry_after is not None else None,
            ) from exc
        except QuotaExceededError as exc:
            raise CrmTransientError(exc.message, details=exc.details) from exc
        except TransientError as exc:
            raise CrmTransientError(
                exc.message, details=exc.details, http_status=_status(exc)
            ) from exc
        except SchemaError as exc:
            raise CrmRejectedError(
                exc.message, details=exc.details, http_status=_status(exc)
            ) from exc
        except AdapterError as exc:
            raise self._classify(exc) from exc

    def _classify(self, exc: AdapterError) -> CrmError:
        status = _status(exc)
        body = str(exc.details.get("body") or "")
        if status == 404:
            return CrmConfigError(
                f"Airtable has no table '{self._table}' in base {self._base_id} (404)",
                details=exc.details,
                http_status=status,
            )
        if status == 422 and any(
            marker in body for marker in ("UNKNOWN_FIELD_NAME", "INVALID_REQUEST_UNKNOWN")
        ):
            return CrmConfigError(
                "Airtable does not know one of the mapped fields; run `make crm-check` "
                "and fix the column names or crm_field_map.airtable.json",
                details=exc.details,
                http_status=status,
            )
        return CrmRejectedError(exc.message, details=exc.details, http_status=status)


def _status(exc: AdapterError) -> int | None:
    value = exc.details.get("status_code")
    return int(value) if isinstance(value, int) else None


def airtable_source_config(settings: Settings | None = None) -> dict[str, Any]:
    config = settings or get_settings()
    return {
        "role": CRM_DESTINATION_ROLE,
        "display_name": "Airtable",
        "terms_url": TERMS_URL,
        "commercial_use_note": "The client's own base, under the client's Airtable plan.",
        "rate_limit": {
            "requests_per_second": config.airtable_rps,
            "burst": max(int(config.airtable_rps), 1),
            "daily_call_cap": config.airtable_daily_call_cap,
            "documented_requests_per_second_per_base": AIRTABLE_DOCUMENTED_RPS,
            "records_per_request": RECORDS_PER_REQUEST,
        },
    }


def airtable_service_source() -> Any:
    from app.modules.audit_web.psi import ServiceSourceSpec

    return ServiceSourceSpec(
        name=AIRTABLE_SOURCE_NAME, kind=SourceKind.api, config=airtable_source_config()
    )


def build_airtable_adapter(
    session: Session,
    settings: Settings | None = None,
    *,
    redis_client: Redis | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    client: httpx.Client | None = None,
    meter: Any = None,
) -> AirtableAdapter:
    """The production wiring: metered, rate limited per the `airtable` source, one attempt."""
    from app.core.redis import get_redis
    from app.modules.discovery.service import api_call_meter

    config = settings or get_settings()
    token = config.airtable_token.get_secret_value()
    http = ApiHttpClient(
        source=AIRTABLE_SOURCE_NAME,
        meter=meter if meter is not None else api_call_meter(None),
        limiter=build_limiter(
            redis_client or get_redis(),
            source=AIRTABLE_SOURCE_NAME,
            requests_per_second=config.airtable_rps,
            burst=max(int(config.airtable_rps), 1),
            daily_call_cap=config.airtable_daily_call_cap,
            clock=clock,
            sleeper=sleeper,
        ),
        max_attempts=1,
        sleeper=sleeper,
        secrets=[token] if token else [],
        client=client,
    )
    return AirtableAdapter(
        http=http,
        token=token,
        base_id=config.airtable_base_id,
        table=config.airtable_table,
        field_map=load_field_map(config.airtable_field_map or None),
    )
