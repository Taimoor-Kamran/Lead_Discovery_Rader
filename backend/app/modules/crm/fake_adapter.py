"""The fake destination: a CRM that lives in our own database (development and tests).

It behaves like a real one — creates, updates only the Radar-owned fields, finds an
existing record by the same keys, flags Do not contact, withdraws — so the whole flow can
be exercised end to end without an account. `crm_fake_records.fields` holds the record
under the same column labels the Airtable template uses.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.crm.adapter import (
    CrmCheck,
    CrmHealth,
    CrmRecord,
    CrmRejectedError,
    CrmResult,
    Destination,
)
from app.modules.crm.fields import (
    LABEL_BY_KEY,
    STATUS_NEW,
    STATUS_WITHDRAWN,
    UPSERT_KEY,
    radar_owned,
)
from app.modules.crm.models import FakeCrmRecord

DNC_LABEL = LABEL_BY_KEY["do_not_contact"]
STATUS_LABEL = LABEL_BY_KEY["status"]
WEBSITE_LABEL = LABEL_BY_KEY["website"]
PHONE_LABEL = LABEL_BY_KEY["public_phone"]
UPSERT_LABEL = LABEL_BY_KEY[UPSERT_KEY]


def labeled(values: dict[str, Any]) -> dict[str, Any]:
    return {LABEL_BY_KEY[key]: value for key, value in values.items() if key in LABEL_BY_KEY}


class FakeCrmAdapter:
    name = Destination.fake.value

    def __init__(self, session: Session) -> None:
        self._session = session

    def check(self) -> CrmHealth:
        count = len(list(self._session.scalars(select(FakeCrmRecord.id))))
        return CrmHealth(
            destination=self.name,
            ok=True,
            checks=[CrmCheck("store", True, f"{count} record(s) in crm_fake_records")],
            message="Destination: fake (demo). Records stay in this database.",
        )

    def upsert(self, record: CrmRecord, existing_external_id: str | None) -> CrmResult:
        if existing_external_id is not None:
            row = self._get(existing_external_id)
            fields = dict(row.fields)
            fields.update(labeled(radar_owned(record.fields)))
            row.fields = fields
            self._session.flush()
            return CrmResult(external_id=str(row.id), action="updated")
        row = FakeCrmRecord(fields=labeled(record.fields))
        self._session.add(row)
        self._session.flush()
        return CrmResult(external_id=str(row.id), action="created")

    def find_by_keys(
        self, radar_business_id: str, domain: str | None, phone: str | None
    ) -> str | None:
        rows = list(self._session.scalars(select(FakeCrmRecord).order_by(FakeCrmRecord.created_at)))
        for row in rows:
            if row.fields.get(UPSERT_LABEL) == radar_business_id:
                return str(row.id)
        if domain:
            for row in rows:
                website = str(row.fields.get(WEBSITE_LABEL) or "").lower()
                if domain.lower() in website:
                    return str(row.id)
        if phone:
            for row in rows:
                if row.fields.get(PHONE_LABEL) == phone:
                    return str(row.id)
        return None

    def mark_do_not_contact(self, external_id: str, note: str, *, flag: bool = True) -> CrmResult:
        row = self._get(external_id)
        row.fields = {**row.fields, DNC_LABEL: flag}
        self._session.flush()
        return CrmResult(external_id=str(row.id), action="updated")

    def withdraw(self, external_id: str) -> CrmResult:
        row = self._get(external_id)
        if row.fields.get(STATUS_LABEL) != STATUS_NEW:
            return CrmResult(external_id=str(row.id), action="unchanged")
        row.fields = {**row.fields, STATUS_LABEL: STATUS_WITHDRAWN}
        self._session.flush()
        return CrmResult(external_id=str(row.id), action="updated")

    def _get(self, external_id: str) -> FakeCrmRecord:
        try:
            key = uuid.UUID(external_id)
        except ValueError as exc:
            raise CrmRejectedError(
                "The fake CRM has no such record", details={"external_id": external_id}
            ) from exc
        row = self._session.get(FakeCrmRecord, key)
        if row is None:
            raise CrmRejectedError(
                "The fake CRM has no such record", details={"external_id": external_id}
            )
        return row


def build_fake_adapter(session: Session, settings: Settings) -> FakeCrmAdapter:
    return FakeCrmAdapter(session)
