"""Phone numbers. A number that does not parse to a valid one becomes `None`, never a guess."""

import phonenumbers

DEFAULT_REGION = "US"


def to_e164(raw: str | None, *, region: str | None = None) -> str | None:
    """Parse a business phone into E.164, or return `None`.

    `region` is the country the address is in; without one the number is read as a US
    number, which is the only market this MVP covers.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw, (region or DEFAULT_REGION).upper())
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
