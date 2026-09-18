"""Addresses: prefer structured components, fall back only as far as is safe.

`formattedAddress` is a display string, not data. When components are missing we read
city, state and postal code off the tail of it and leave the street parts `None` rather
than split a line that may not be a street line at all.
"""

import re

from app.modules.adapters.base import AddressPart
from app.modules.normalization.schemas import Address

# Only the two-word forms that are unambiguous as whole words.
STREET_ABBREVIATIONS = {
    "st": "street",
    "str": "street",
    "ave": "avenue",
    "av": "avenue",
    "rd": "road",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "cir": "circle",
    "pkwy": "parkway",
    "pky": "parkway",
    "hwy": "highway",
    "sq": "square",
    "ter": "terrace",
    "trl": "trail",
    "pl": "place",
    "expy": "expressway",
    "ste": "suite",
    "apt": "apartment",
    "bldg": "building",
    "fl": "floor",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}

US_STATES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "puerto rico": "PR",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
STATE_CODES = frozenset(US_STATES.values())

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")
# "Austin, TX 78701-1234" / "Austin, TX 78701" at the end of a formatted address.
_CITY_STATE_ZIP = re.compile(
    r"(?P<city>[^,]+),\s*(?P<state>[A-Za-z .]+?)\s+(?P<postal>\d{5}(?:-\d{4})?)\s*$"
)


def state_code(raw: str | None) -> str | None:
    """A 2-letter USPS code, or `None` when the value is not a state we recognise."""
    if not raw:
        return None
    value = _WHITESPACE.sub(" ", raw.strip())
    if value.upper() in STATE_CODES:
        return value.upper()
    return US_STATES.get(value.lower().replace(".", ""))


def postal_code(raw: str | None, *, country: str | None = None) -> str | None:
    """US postal codes lose their +4; anything else is kept as it was given."""
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    if (country or "US").upper() == "US":
        match = re.match(r"^(\d{5})(?:-\d{4})?$", value)
        return match.group(1) if match else None
    return value


def street_key(*parts: str | None) -> str | None:
    """A comparable street identity: lowercase, no punctuation, abbreviations expanded."""
    joined = " ".join(part for part in parts if part)
    if not joined.strip():
        return None
    folded = _PUNCTUATION.sub(" ", joined.lower())
    words = [STREET_ABBREVIATIONS.get(word, word) for word in _WHITESPACE.sub(" ", folded).split()]
    return " ".join(words) or None


def from_components(
    components: list[AddressPart] | None, *, formatted: str | None = None
) -> Address:
    """Build an address from Places `addressComponents`, filling gaps from `formatted`."""
    by_type: dict[str, AddressPart] = {}
    for part in components or ():
        for type_name in part.types:
            by_type.setdefault(type_name, part)

    def long(type_name: str) -> str | None:
        part = by_type.get(type_name)
        return part.long_text if part else None

    def short(type_name: str) -> str | None:
        part = by_type.get(type_name)
        return part.short_text or part.long_text if part else None

    street_number = long("street_number")
    route = long("route")
    subpremise = long("subpremise")
    country = short("country")

    line1 = " ".join(p for p in (street_number, route) if p) or None
    line2 = f"Suite {subpremise}" if subpremise else None

    fallback = _from_formatted(formatted)
    city = long("locality") or long("postal_town") or fallback.city
    state = state_code(short("administrative_area_level_1")) or fallback.state
    postal = postal_code(long("postal_code"), country=country) or fallback.postal_code

    return Address(
        line1=line1,
        line2=line2,
        street_key=street_key(street_number, route),
        city=city,
        state=state,
        postal_code=postal,
        country=(country or fallback.country or None),
    )


def _from_formatted(formatted: str | None) -> Address:
    """City, state and postal code only. The street parts stay unknown on purpose."""
    empty = Address()
    if not formatted:
        return empty

    text = formatted.strip()
    # Drop a trailing country name so "…, Austin, TX 78701, USA" still matches.
    trimmed = re.sub(r",\s*(USA|United States)\.?\s*$", "", text, flags=re.IGNORECASE)
    match = _CITY_STATE_ZIP.search(trimmed)
    if match is None:
        return empty
    state = state_code(match.group("state"))
    if state is None:
        return empty
    return Address(
        city=_WHITESPACE.sub(" ", match.group("city")).strip() or None,
        state=state,
        postal_code=postal_code(match.group("postal")),
        country="US" if trimmed != text else None,
    )
