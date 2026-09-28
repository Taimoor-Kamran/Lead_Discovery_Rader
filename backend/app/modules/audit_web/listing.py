"""What the homepage says against what the business's listing says (spec v0.12.0, item 8).

Every comparison here can accuse a business of something in front of a salesperson who
will repeat it, and a false accusation is worse than no finding at all. So three rules
hold throughout:

* **Both sides go through the same normaliser.** Phones through `normalization.phones`,
  addresses through the street-key rules in `normalization.addresses`, websites through
  `normalization.web` — the code that already decides whether two records are one business.
* **Only a conflict between two known values is a mismatch.** A value missing on either
  side, one that does not parse, or a site that describes several locations is
  `not_compared`, with the reason written down — never a guess in either direction.
* **A mismatch says the two differ, never which is wrong.** A call-tracking number on the
  site is a different number from the listing's; that is true and worth knowing, and the
  finding claims nothing more.

The result is a check (`listing_comparison`) like any other, so a reviewer can see why a
comparison did or did not become a finding.
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import phonenumbers

from app.modules.audit_web.checks import CheckResult, Checks, value_of
from app.modules.normalization import names, phones
from app.modules.normalization.addresses import street_key
from app.modules.normalization.web import host_of, registered_domain

MATCH = "match"
MISMATCH = "mismatch"
NOT_COMPARED = "not_compared"

# A unit designator and the unit it names: "Suite #11", "Ste 303", "#4B", "Unit 2". The
# unit must contain a digit: a listing stored "Suite Suite 402" in production, and reading
# the second "Suite" as the unit accused a business of a wrong address (v0.12.0 canary). No
# "Fl"/"Floor": in a one-line address "Miami, FL 33101" would read as unit 33101.
_UNIT = re.compile(
    r"(?:\b(?:suite|ste|unit|apt|apartment|room|rm|bldg|building)\b\.?\s*#?|#)\s*"
    r"([a-z0-9-]*\d[a-z0-9-]*)",
    re.IGNORECASE,
)
_HOUSE_NUMBER = re.compile(r"^\s*(\d+[a-z]?)\b", re.IGNORECASE)
_POSTAL = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_PO_BOX = re.compile(r"\bp\.?\s*o\.?\s*box\b", re.IGNORECASE)


@dataclass(frozen=True)
class Listing:
    """The business's own record: the facts a homepage is compared against."""

    name: str | None = None
    phone_e164: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    website: str | None = None


def compare(checks: Checks, listing: Listing) -> CheckResult:
    """Phone, address and website, each `match`, `mismatch` or `not_compared` with why."""
    url = value_of(checks, "final_url")
    results = {
        "phone": compare_phone(checks, listing),
        "address": compare_address(checks, listing),
        "website": compare_website(checks, listing),
    }
    summary = "; ".join(
        f"{key}: {result['status']}" + (f" ({result['reason']})" if result.get("reason") else "")
        for key, result in results.items()
    )
    return CheckResult(results, evidence_text=summary, evidence_url=url)


# --- phone ----------------------------------------------------------------------------


def compare_phone(checks: Checks, listing: Listing) -> dict[str, Any]:
    if not listing.phone_e164:
        return _not_compared("the listing has no phone number")
    raw_numbers = value_of(checks, "tel_numbers") or []
    if not raw_numbers:
        return _not_compared("the homepage has no tel: link")
    parsed = [(raw, phones.to_e164(raw, region=listing.country)) for raw in raw_numbers if raw]
    valid = [(raw, e164) for raw, e164 in parsed if e164]
    if not valid:
        return _not_compared(
            f"the homepage tel: link {raw_numbers[0]!r} does not parse as a phone number"
        )
    shown_listing = national(listing.phone_e164)
    if any(e164 == listing.phone_e164 for _, e164 in valid):
        return {"status": MATCH, "site": national(valid[0][1]), "listing": shown_listing}
    return {
        "status": MISMATCH,
        "site": national(valid[0][1]),
        "site_raw": valid[0][0],
        "listing": shown_listing,
    }


def national(e164: str) -> str:
    """`+15125550100` → `(512) 555-0100`: the number as a person reads it."""
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return e164
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)


# --- address --------------------------------------------------------------------------


@dataclass(frozen=True)
class AddressParts:
    """The parts of an address that are compared: numbers, never street-name spellings."""

    house_number: str | None
    unit: str | None
    postal_code: str | None
    shown: str


def compare_address(checks: Checks, listing: Listing) -> dict[str, Any]:
    """The LocalBusiness address against the listing's, by house number, unit and ZIP.

    Street *names* are not compared: "US-290" and "Hwy 290" are one road spelled two ways,
    and no normaliser we have can prove otherwise. The numbers can be compared exactly.
    Unit designators are unified ("Suite #11", "Ste 11", "#11" are all unit 11).
    """
    local = value_of(checks, "local_business")
    if not isinstance(local, dict):
        return _not_compared("the homepage has no LocalBusiness structured data")
    if int(local.get("items") or 1) > 1:
        return _not_compared(f"the homepage describes {local.get('items')} businesses or locations")
    address = local.get("address")
    if not isinstance(address, dict):
        return _not_compared("the homepage's LocalBusiness data carries no address")
    site = site_address(address)
    if site is None:
        return _not_compared("the homepage's address has no street number to compare")
    ours = listing_address(listing)
    if ours is None:
        return _not_compared("the listing has no street address")

    differences: list[str] = []
    for label, left, right in (
        ("street number", site.house_number, ours.house_number),
        ("unit", site.unit, ours.unit),
        ("ZIP code", site.postal_code, ours.postal_code),
    ):
        if left is not None and right is not None and left != right:
            differences.append(label)
    status = MISMATCH if differences else MATCH
    result: dict[str, Any] = {"status": status, "site": site.shown, "listing": ours.shown}
    if differences:
        result["differs_in"] = differences
    return result


def site_address(address: dict[str, Any]) -> AddressParts | None:
    street = str(address.get("street") or address.get("text") or "").strip()
    if not street or _PO_BOX.search(street):
        return None
    house = _house_number(street)
    if house is None:
        return None
    postal = address.get("postal_code")
    postal_match = _POSTAL.search(str(postal or ""))
    postal_code = (
        postal_match.group(1)
        if postal_match
        else (_postal_from_text(street) if "text" in address else None)
    )
    shown = ", ".join(
        str(part)
        for part in (
            street,
            address.get("locality"),
            address.get("region"),
            address.get("postal_code"),
        )
        if part
    )
    return AddressParts(
        house_number=house,
        unit=_unit(street),
        postal_code=postal_code,
        shown=shown,
    )


def listing_address(listing: Listing) -> AddressParts | None:
    if not listing.address_line1:
        return None
    house = _house_number(listing.address_line1)
    if house is None:
        return None
    postal = _POSTAL.search(listing.postal_code or "")
    shown = ", ".join(
        part
        for part in (
            listing.address_line1,
            listing.address_line2,
            listing.city,
            listing.state,
            listing.postal_code,
        )
        if part
    )
    return AddressParts(
        house_number=house,
        unit=_unit(
            " ".join(part for part in (listing.address_line1, listing.address_line2) if part)
        ),
        postal_code=postal.group(1) if postal else None,
        shown=shown,
    )


def _postal_from_text(text: str) -> str | None:
    """The ZIP code of a one-line address: the last five digits *after* the first comma.

    Never the first five-digit number, which in "12345 Research Blvd, Austin, TX 78759" is
    the street number.
    """
    _, comma, rest = text.partition(",")
    if not comma:
        return None
    matches = _POSTAL.findall(rest)
    return matches[-1] if matches else None


def _house_number(street: str) -> str | None:
    match = _HOUSE_NUMBER.match(street_key(street) or "")
    return match.group(1).lower() if match else None


def _unit(text: str) -> str | None:
    match = _UNIT.search(text)
    if match is None:
        return None
    # "Suite 011" and "Suite 11" are one unit; "11B" and "11b" too.
    return match.group(1).lower().lstrip("0") or "0"


# --- website --------------------------------------------------------------------------


def compare_website(checks: Checks, listing: Listing) -> dict[str, Any]:
    """The listing's website URL against the address the homepage says is its own.

    The homepage's own address is its canonical URL when that is on the same registered
    domain as the page actually served, and the served URL otherwise — a canonical tag
    pointing at another domain is a template leftover more often than a statement.
    Two differences are reported: the listing says `http://` while the site is served over
    https, and the listing's host differs from the site's only by `www.`.
    """
    if not listing.website:
        return _not_compared("the listing has no website")
    final_url = value_of(checks, "final_url")
    if not final_url:
        return _not_compared("the homepage was not loaded")
    site_url = final_url
    canonical = value_of(checks, "canonical_url")
    if isinstance(canonical, str) and _same_registered_domain(canonical, final_url):
        site_url = canonical

    listed = urlsplit(listing.website if "//" in listing.website else f"//{listing.website}")
    site = urlsplit(site_url)
    listed_host = (listed.hostname or "").lower().rstrip(".")
    site_host = (site.hostname or "").lower().rstrip(".")
    if not listed_host or not site_host:
        return _not_compared("a website address has no host")
    if not _same_registered_domain(listing.website, site_url):
        return _not_compared(
            f"the listing's website ({listed_host}) and the homepage ({site_host}) are on "
            "different domains"
        )

    differences: list[str] = []
    if (listed.scheme or "").lower() == "http" and site.scheme.lower() == "https":
        differences.append("http")
    if listed_host != site_host and host_of(listed_host) == host_of(site_host):
        differences.append("www")
    result: dict[str, Any] = {
        "status": MISMATCH if differences else MATCH,
        "site": site_url,
        "listing": listing.website,
    }
    if differences:
        result["differs_in"] = differences
    return result


def _same_registered_domain(left: str, right: str) -> bool:
    left_host, right_host = host_of(left), host_of(right)
    if left_host is None or right_host is None:
        return False
    return (registered_domain(left_host) or left_host) == (
        registered_domain(right_host) or right_host
    )


# --- the business name ------------------------------------------------------------------

# Words in a business name that say nothing about which business it is.
GENERIC_NAME_WORDS = frozenset(
    {"the", "and", "of", "a", "an", "services", "service", "company", "group", "co"}
)
# The shortest shared word start that counts as naming the business: "electric" names
# "Electrical Services", "plumb" names both "Plumber" and "Plumbing".
MIN_SHARED_PREFIX = 5
MIN_WORD_CHARS = 3


def name_words(name: str | None) -> list[str]:
    """The words of a business name that identify it, normalised as resolution does."""
    normalized = names.normalize_name(name) or ""
    return [
        word
        for word in normalized.split()
        if len(word) >= MIN_WORD_CHARS and word not in GENERIC_NAME_WORDS
    ]


def text_names_business(text: str, name: str | None) -> bool | None:
    """Whether `text` carries a word of the business name. `None` when the name has none.

    Lenient on purpose, because "no" becomes a finding: a shared word start of five letters
    counts, and so does a name word run together in a domain-style title
    ("UltimatePlumber.com").
    """
    wanted = name_words(name)
    if not wanted:
        return None
    normalized = names.normalize_name(text) or ""
    words = normalized.split()
    squashed = normalized.replace(" ", "")
    for word in wanted:
        if len(word) >= 4 and word in squashed:
            return True
        for candidate in words:
            if candidate == word:
                return True
            shared = len(_common_prefix(candidate, word))
            if shared >= MIN_SHARED_PREFIX or (
                shared >= 4 and shared == min(len(candidate), len(word))
            ):
                return True
    return False


def _common_prefix(left: str, right: str) -> str:
    size = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        size += 1
    return left[:size]


def _not_compared(reason: str) -> dict[str, Any]:
    return {"status": NOT_COMPARED, "reason": reason}
