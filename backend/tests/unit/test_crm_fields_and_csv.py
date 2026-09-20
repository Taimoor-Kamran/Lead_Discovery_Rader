"""Field ownership, the CSV renderer and the small payload formatters. No database."""

import csv
import io
import uuid
from datetime import UTC, datetime

import pytest

from app.modules.crm import fields
from app.modules.crm.adapter import CrmRecord
from app.modules.crm.csv_adapter import (
    BOM,
    COLUMNS,
    export_filename,
    protect_cell,
    render_csv,
)
from app.modules.crm.payload import (
    clip,
    finding_label,
    format_phone,
    humanize,
    payload_hash,
    service_label,
)


def test_every_field_has_one_owner_and_the_crm_owned_ones_are_exactly_the_blueprint_four() -> None:
    assert set(fields.CRM_OWNED) == {"assigned_rep", "status", "follow_up_date", "notes"}
    assert set(fields.RADAR_OWNED) | set(fields.CRM_OWNED) == set(fields.FIELD_BY_KEY)
    assert not set(fields.RADAR_OWNED) & set(fields.CRM_OWNED)
    assert fields.UPSERT_KEY in fields.RADAR_OWNED
    assert len({item.label for item in fields.FIELDS}) == len(fields.FIELDS), "labels are unique"


def test_an_update_payload_never_carries_a_crm_owned_field() -> None:
    values = fields.for_create(
        {"radar_business_id": "b", "assigned_rep": "rep@example.com", "status": "Won", "notes": "x"}
    )
    update = fields.radar_owned(values)
    assert "radar_business_id" in update
    assert not set(update) & set(fields.CRM_OWNED)
    assert fields.crm_owned(values) == {
        "assigned_rep": "rep@example.com",
        "status": "Won",
        "follow_up_date": None,
        "notes": "x",
    }


def test_the_payload_hash_ignores_crm_owned_fields_and_is_order_independent() -> None:
    business_id = uuid.uuid4()
    one = CrmRecord(business_id, {"business_name": "A", "city": "Austin", "status": "New"})
    two = CrmRecord(business_id, {"city": "Austin", "business_name": "A", "status": "Won"})
    three = CrmRecord(business_id, {"city": "Dallas", "business_name": "A", "status": "New"})
    assert payload_hash(one) == payload_hash(two)
    assert payload_hash(one) != payload_hash(three)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("+15125550102", "(512) 555-0102"),
        ("+442071234567", "+442071234567"),
        (None, None),
        ("", None),
    ],
)
def test_us_phones_are_formatted_and_anything_else_is_passed_through(
    raw: str | None, shown: str | None
) -> None:
    assert format_phone(raw) == shown


def test_labels_are_plain_words_never_codes() -> None:
    assert humanize("general_contracting") == "General contracting"
    assert humanize(None) is None
    assert service_label("website_design") == "Website redesign"
    assert service_label("brand_new_service") == "Brand new service"
    assert finding_label("no_https") == "No HTTPS"
    assert finding_label("something_else") == "Something else"
    assert clip("x" * 20, 10) == "x" * 9 + "…"
    assert clip("short", 10) == "short"


@pytest.mark.parametrize(
    ("value", "cell"),
    [
        (
            '=HYPERLINK("http://evil.invalid","click")',
            '\'=HYPERLINK("http://evil.invalid","click")',
        ),
        ("+1 512 555 0102", "'+1 512 555 0102"),
        ("-5", "'-5"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("Barton Creek Plumbing", "Barton Creek Plumbing"),
        (None, ""),
        (True, "true"),
        (False, "false"),
        (72, "72"),
    ],
)
def test_formula_prefixes_are_neutralised(value: object, cell: str) -> None:
    assert protect_cell(value) == cell


def test_the_csv_has_a_bom_a_header_and_rfc_4180_quoting() -> None:
    rows = [
        {
            "Radar Business ID": "b1",
            "Business name": '=HYPERLINK("http://evil.invalid","Plumb, "Bob"")',
            "Why this is a lead": "Line one\nLine two",
            "Do not contact": False,
            "Lead score": 72,
            "Public phone": "(512) 555-0102",
        }
    ]
    data = b"".join(render_csv(rows)).decode("utf-8")
    assert data.startswith(BOM), "Excel needs the BOM to read UTF-8"
    parsed = list(csv.reader(io.StringIO(data.removeprefix(BOM))))
    assert parsed[0] == list(COLUMNS)
    assert parsed[0][0] == "Radar Business ID" and "Assigned rep" in parsed[0]
    row = dict(zip(parsed[0], parsed[1], strict=True))
    assert row["Business name"].startswith("'=HYPERLINK"), "the formula is text, not a formula"
    assert row["Why this is a lead"] == "Line one\nLine two"
    assert row["Do not contact"] == "false"
    assert row["Lead score"] == "72"
    assert row["Notes"] == ""
    assert data.count("\r\n") == 2


def test_the_export_filename_is_stamped_in_utc() -> None:
    assert (
        export_filename(datetime(2026, 9, 20, 14, 5, tzinfo=UTC)) == "radar-leads-20260920-1405.csv"
    )
