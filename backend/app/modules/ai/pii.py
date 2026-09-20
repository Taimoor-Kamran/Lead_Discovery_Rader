"""Finding and removing the things the AI must never be sent and must never say.

The same three patterns serve both directions. Going out, they scrub the input so that no
phone number, email address or street address reaches the provider even when a homepage
prints one in its visible text. Coming back, the guardrails use them to catch an answer
that contains one anyway — which, since none went in, can only have been invented.

The patterns lean towards catching too much. A year range such as `2018-2024` looks like
a phone number to the digit pattern and is removed; a quote the model wanted to make of
that span then fails the verbatim check and is dropped. That is the right failure: the
cost of over-scrubbing is one lost quote, the cost of under-scrubbing is a phone number in
a prompt.
"""

import re
from collections.abc import Iterable

PHONE_REMOVED = "[phone removed]"
EMAIL_REMOVED = "[email removed]"
ADDRESS_REMOVED = "[address removed]"
MIN_PHONE_DIGITS = 7

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A run of digits with the separators phone numbers use, at least seven digits long.
# Anchored on digits at both ends so surrounding punctuation stays where it was.
PHONE_PATTERN = re.compile(r"\+?\(?\d[\d\s().\-]{5,}\d")
URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"'()]+", re.IGNORECASE)
_STREET_SUFFIXES = (
    "street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|way|court|ct|highway|"
    "hwy|parkway|pkwy|place|pl|trail|trl|circle|cir|terrace|ter|square|sq|loop|expressway|"
    "expy|freeway|fwy|route|rte"
)
# A building number, up to four capitalised words, and a street suffix: `100 Congress Ave`,
# `1200 South Lamar Boulevard`. Case-insensitive on the suffix; the words before it are
# whatever the page wrote.
ADDRESS_PATTERN = re.compile(
    rf"\b\d{{1,6}}[A-Za-z]?\s+(?:[A-Za-z0-9'.\-]+\s+){{0,4}}(?:{_STREET_SUFFIXES})\b\.?",
    re.IGNORECASE,
)


def _is_phone(match: re.Match[str]) -> bool:
    return sum(char.isdigit() for char in match.group(0)) >= MIN_PHONE_DIGITS


def scrub_phones(text: str) -> str:
    return PHONE_PATTERN.sub(lambda m: PHONE_REMOVED if _is_phone(m) else m.group(0), text)


def scrub_emails(text: str) -> str:
    return EMAIL_PATTERN.sub(EMAIL_REMOVED, text)


def scrub_addresses(text: str, known: Iterable[str] = ()) -> str:
    """Remove street addresses, including the literal ones we already know for the business."""
    for literal in known:
        cleaned = " ".join(literal.split())
        if len(cleaned) >= 4:
            text = re.sub(re.escape(cleaned), ADDRESS_REMOVED, text, flags=re.IGNORECASE)
    return ADDRESS_PATTERN.sub(ADDRESS_REMOVED, text)


def scrub(text: str, *, known_addresses: Iterable[str] = ()) -> str:
    """Everything the input builder does to a string before it leaves the machine."""
    return scrub_addresses(scrub_emails(scrub_phones(text)), known_addresses)


def find_emails(text: str) -> list[str]:
    return EMAIL_PATTERN.findall(text)


def find_phones(text: str) -> list[str]:
    return [m.group(0) for m in PHONE_PATTERN.finditer(text) if _is_phone(m)]


def find_urls(text: str) -> list[str]:
    return URL_PATTERN.findall(text)


def find_addresses(text: str) -> list[str]:
    return [m.group(0) for m in ADDRESS_PATTERN.finditer(text)]
