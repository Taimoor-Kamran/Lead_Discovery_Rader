"""Provider categories to our own industry slugs.

Anything not in the table becomes `other` and keeps its raw types, so a category we have
not mapped yet is visible rather than silently renamed.
"""

import re

from app.modules.normalization.schemas import BusinessStatus

OTHER = "other"

# Places `primaryType` / `types` → internal slug. Longest-standing trades first; the map
# is data, so adding a trade is a one-line change and not a code change.
PLACES_TYPE_TO_INDUSTRY: dict[str, str] = {
    "plumber": "plumbing",
    "electrician": "electrical",
    "roofing_contractor": "roofing",
    "general_contractor": "general_contracting",
    "hvac_contractor": "hvac",
    "painter": "painting",
    "locksmith": "locksmith",
    "moving_company": "moving",
    "storage": "storage",
    "landscaper": "landscaping",
    "pest_control_service": "pest_control",
    "house_cleaning_service": "cleaning",
    "laundry": "laundry",
    "car_repair": "auto_repair",
    "car_dealer": "auto_sales",
    "car_wash": "car_wash",
    "dentist": "dental",
    "dental_clinic": "dental",
    "doctor": "medical",
    "physiotherapist": "physiotherapy",
    "chiropractor": "chiropractic",
    "veterinary_care": "veterinary",
    "pharmacy": "pharmacy",
    "lawyer": "legal",
    "accounting": "accounting",
    "insurance_agency": "insurance",
    "real_estate_agency": "real_estate",
    "bank": "banking",
    "restaurant": "restaurant",
    "cafe": "cafe",
    "coffee_shop": "cafe",
    "bakery": "bakery",
    "bar": "bar",
    "hair_care": "hair_salon",
    "hair_salon": "hair_salon",
    "beauty_salon": "beauty",
    "nail_salon": "beauty",
    "spa": "spa",
    "gym": "fitness",
    "fitness_center": "fitness",
    "school": "education",
    "child_care_agency": "childcare",
    "lodging": "lodging",
    "hotel": "lodging",
    "florist": "florist",
    "pet_store": "pet_retail",
    "furniture_store": "furniture_retail",
    "hardware_store": "hardware_retail",
    "clothing_store": "clothing_retail",
    "grocery_store": "grocery",
    "supermarket": "grocery",
}

# Free-text fallback for sources that have no type vocabulary of their own.
TEXT_TO_INDUSTRY: dict[str, str] = {
    "plumber": "plumbing",
    "plumbing": "plumbing",
    "electrician": "electrical",
    "electrical": "electrical",
    "roofer": "roofing",
    "roofing": "roofing",
    "hvac": "hvac",
    "contractor": "general_contracting",
    "dentist": "dental",
    "dental": "dental",
    "lawyer": "legal",
    "attorney": "legal",
    "legal": "legal",
    "restaurant": "restaurant",
    "landscaping": "landscaping",
    "cleaning": "cleaning",
}

PLACES_STATUS_TO_STATUS = {
    "OPERATIONAL": BusinessStatus.operational,
    "CLOSED_TEMPORARILY": BusinessStatus.closed_temporarily,
    "CLOSED_PERMANENTLY": BusinessStatus.closed_permanently,
}

_WORD = re.compile(r"[a-z0-9]+")


def to_industry(primary_type: str | None, types: list[str] | None = None) -> str:
    """The first type we recognise wins; `primaryType` is asked first. Unmapped → `other`."""
    for value in [primary_type, *(types or [])]:
        if not value:
            continue
        mapped = PLACES_TYPE_TO_INDUSTRY.get(value.strip().lower())
        if mapped:
            return mapped
    return OTHER


def from_text(text: str | None) -> str:
    """Map free text (a search job's industry, a feed's category) onto a slug."""
    if not text:
        return OTHER
    words = _WORD.findall(text.lower())
    for word in words:
        mapped = TEXT_TO_INDUSTRY.get(word)
        if mapped:
            return mapped
    return OTHER


def to_business_status(raw: str | None) -> BusinessStatus:
    """Anything we do not recognise is `unknown` — never assumed to be open."""
    if not raw:
        return BusinessStatus.unknown
    return PLACES_STATUS_TO_STATUS.get(raw.strip().upper(), BusinessStatus.unknown)
