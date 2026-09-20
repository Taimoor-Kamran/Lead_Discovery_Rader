"""The CSV destination: works today, needs no account.

There is no remote system, so the `crm_leads` row *is* the record: `upsert` hands back a
stable id, and `GET /crm/export.csv` renders the eligible rows on demand. The file opens
correctly in Excel (UTF-8 with a BOM, RFC 4180 quoting) and a cell that starts with a
formula character is neutralised, because a business name is untrusted input and a
spreadsheet is an interpreter.
"""

import csv
import io
import uuid
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.businesses.models import Business
from app.modules.crm.adapter import CrmCheck, CrmHealth, CrmRecord, CrmResult, Destination
from app.modules.crm.fields import FIELDS
from app.modules.crm.models import CrmLead, CrmLeadStatus

# A cell beginning with one of these would be executed by a spreadsheet, not displayed.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"
COLUMNS: tuple[str, ...] = tuple(item.label for item in FIELDS)
EXPORTED_STATUSES = (CrmLeadStatus.synced, CrmLeadStatus.withdrawn)


def protect_cell(value: Any) -> str:
    """Render a value for a CSV cell, defusing formula injection with a leading quote."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def render_csv(rows: Iterable[Mapping[str, Any]]) -> Iterator[bytes]:
    """The whole file, header first, as UTF-8 chunks. Every value goes through `protect_cell`."""
    yield BOM.encode("utf-8")
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    yield buffer.getvalue().encode("utf-8")
    for row in rows:
        buffer.seek(0)
        buffer.truncate()
        writer.writerow([protect_cell(row.get(column)) for column in COLUMNS])
        yield buffer.getvalue().encode("utf-8")


def export_filename(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"radar-leads-{moment.astimezone(UTC):%Y%m%d-%H%M}.csv"


def external_id_for(business_id: uuid.UUID | str) -> str:
    return f"csv:{business_id}"


class CsvExportAdapter:
    name = Destination.csv.value

    def __init__(self, session: Session) -> None:
        self._session = session

    def check(self) -> CrmHealth:
        waiting = len(
            list(
                self._session.scalars(
                    select(CrmLead.id).where(
                        CrmLead.destination == self.name,
                        CrmLead.status.in_(EXPORTED_STATUSES),
                        CrmLead.export_batch_id.is_(None),
                    )
                )
            )
        )
        return CrmHealth(
            destination=self.name,
            ok=True,
            checks=[CrmCheck("export", True, f"{waiting} lead(s) waiting for the next export")],
            message="Destination: CSV download. Nothing leaves until someone exports.",
        )

    def upsert(self, record: CrmRecord, existing_external_id: str | None) -> CrmResult:
        if existing_external_id is not None:
            return CrmResult(external_id=existing_external_id, action="updated")
        return CrmResult(external_id=external_id_for(record.business_id), action="created")

    def find_by_keys(
        self, radar_business_id: str, domain: str | None, phone: str | None
    ) -> str | None:
        """A business already exported once, by id, then by domain, then by phone."""
        from app.modules.crm.payload import format_phone

        rows = self._session.execute(
            select(CrmLead.external_id, Business)
            .join(Business, Business.id == CrmLead.business_id)
            .where(CrmLead.destination == self.name, CrmLead.external_id.is_not(None))
            .order_by(CrmLead.created_at.asc())
        ).all()
        for external_id, business in rows:
            if str(business.id) == radar_business_id:
                return str(external_id)
        if domain:
            for external_id, business in rows:
                if business.domain and business.domain.lower() == domain.lower():
                    return str(external_id)
        if phone:
            for external_id, business in rows:
                if format_phone(business.phone_e164) == phone:
                    return str(external_id)
        return None

    def mark_do_not_contact(self, external_id: str, note: str, *, flag: bool = True) -> CrmResult:
        # The export renders the live flag, so there is nothing to write here.
        return CrmResult(external_id=external_id, action="updated")

    def withdraw(self, external_id: str) -> CrmResult:
        return CrmResult(external_id=external_id, action="updated")


def build_csv_adapter(session: Session, settings: Settings) -> CsvExportAdapter:
    return CsvExportAdapter(session)
