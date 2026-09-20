"""The CRM record (blueprint slide 40): every field, and who owns it.

Ownership is the rule that keeps two systems from fighting. A **radar** field is a fact
Radar knows better than anyone (the business, the services, the score, the evidence), so
every sync writes it. A **crm** field belongs to the salespeople: it is written **once**,
when the record is created, and never again — whatever they typed in the CRM stays.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class Owner(enum.StrEnum):
    radar = "radar"
    crm = "crm"


class Kind(enum.StrEnum):
    """The shape of a value, for the CRM's column type (and `crm-check`'s compatibility)."""

    text = "text"
    long_text = "long_text"
    number = "number"
    checkbox = "checkbox"
    date = "date"
    url = "url"


@dataclass(frozen=True)
class CrmField:
    key: str
    label: str
    owner: Owner
    kind: Kind
    description: str


FIELDS: tuple[CrmField, ...] = (
    CrmField("radar_business_id", "Radar Business ID", Owner.radar, Kind.text, "The upsert key."),
    CrmField("business_name", "Business name", Owner.radar, Kind.text, "Survivorship value."),
    CrmField("legal_name", "Legal name", Owner.radar, Kind.text, "When a source gave one."),
    CrmField("industry", "Industry", Owner.radar, Kind.text, "Plain label, e.g. Plumbing."),
    CrmField("city", "City", Owner.radar, Kind.text, ""),
    CrmField("state", "State", Owner.radar, Kind.text, ""),
    CrmField("postal_code", "Postal code", Owner.radar, Kind.text, ""),
    CrmField("address", "Address", Owner.radar, Kind.text, "Street address."),
    CrmField("website", "Website", Owner.radar, Kind.url, ""),
    CrmField("public_phone", "Public phone", Owner.radar, Kind.text, "Formatted (512) 555-0102."),
    CrmField(
        "services",
        "Services",
        Owner.radar,
        Kind.text,
        "Approved services, e.g. Website redesign; Online booking.",
    ),
    CrmField("lead_score", "Lead score", Owner.radar, Kind.number, "Highest approved, 0-100."),
    CrmField(
        "why_lead",
        "Why this is a lead",
        Owner.radar,
        Kind.long_text,
        "The rules' reasons for the approved services (at most 1000 characters).",
    ),
    CrmField(
        "top_findings",
        "Top findings",
        Owner.radar,
        Kind.long_text,
        "Plain-language findings from the latest website audit.",
    ),
    CrmField("source", "Source", Owner.radar, Kind.text, "e.g. Google Places."),
    CrmField("source_url", "Source URL", Owner.radar, Kind.url, "The listing."),
    CrmField("radar_link", "Radar link", Owner.radar, Kind.url, "The lead page in Radar."),
    CrmField("date_discovered", "Date discovered", Owner.radar, Kind.date, ""),
    CrmField("date_approved", "Date approved", Owner.radar, Kind.date, ""),
    CrmField("approved_by", "Approved by", Owner.radar, Kind.text, "Reviewer email."),
    CrmField(
        "do_not_contact",
        "Do not contact",
        Owner.radar,
        Kind.checkbox,
        "Propagated from suppressions; the record is never deleted.",
    ),
    CrmField("assigned_rep", "Assigned rep", Owner.crm, Kind.text, "Set on create only."),
    CrmField("status", "Status", Owner.crm, Kind.text, "New on create; never overwritten."),
    CrmField("follow_up_date", "Follow-up date", Owner.crm, Kind.date, "Empty on create."),
    CrmField("notes", "Notes", Owner.crm, Kind.long_text, "Reviewer's approval note on create."),
)

FIELD_BY_KEY: dict[str, CrmField] = {item.key: item for item in FIELDS}
LABEL_BY_KEY: dict[str, str] = {item.key: item.label for item in FIELDS}
RADAR_OWNED: tuple[str, ...] = tuple(item.key for item in FIELDS if item.owner is Owner.radar)
CRM_OWNED: tuple[str, ...] = tuple(item.key for item in FIELDS if item.owner is Owner.crm)
UPSERT_KEY = "radar_business_id"
STATUS_NEW = "New"
STATUS_WITHDRAWN = "Withdrawn"
WHY_LEAD_MAX_CHARS = 1000


def radar_owned(values: Mapping[str, Any]) -> dict[str, Any]:
    """The fields an update may carry. CRM-owned keys are dropped, whatever they hold."""
    return {key: values[key] for key in RADAR_OWNED if key in values}


def crm_owned(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: values[key] for key in CRM_OWNED if key in values}


def for_create(values: Mapping[str, Any]) -> dict[str, Any]:
    """Everything, in the canonical order."""
    return {item.key: values.get(item.key) for item in FIELDS}
