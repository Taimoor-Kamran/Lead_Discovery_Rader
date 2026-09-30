"""SPF and DMARC, read only as far as the findings need (spec v0.13.0, decision C8).

A TXT record can hold anything, and a DMARC record's `rua=` / `ruf=` tags are `mailto:`
addresses — often a person's own mailbox. So nothing here returns a raw record except an
SPF record (`v=spf1 …`, which names mail servers, not people); a DMARC record comes back as
its `v`, `p` and `sp` tags only, and every other TXT record is only counted.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_SPF = re.compile(r"^v=spf1(\s|$)", re.IGNORECASE)
_DMARC = re.compile(r"^v=DMARC1\s*(;|$)", re.IGNORECASE)
_ALL = re.compile(r"^([+\-~?]?)all$", re.IGNORECASE)
# The only DMARC tags ever kept. Never `rua` or `ruf`.
DMARC_KEPT_TAGS = ("v", "p", "sp")


@dataclass(frozen=True)
class ApexTxt:
    spf: tuple[str, ...]
    other_count: int


@dataclass(frozen=True)
class DmarcTxt:
    records: tuple[dict[str, str], ...]
    other_count: int


def split_apex_txt(values: Iterable[str]) -> ApexTxt:
    """Keep the SPF records; count the rest and forget them."""
    spf: list[str] = []
    other = 0
    for value in values:
        if _SPF.match(value.strip()):
            spf.append(value.strip())
        else:
            other += 1
    return ApexTxt(tuple(spf), other)


def split_dmarc_txt(values: Iterable[str]) -> DmarcTxt:
    """Parse each `v=DMARC1` record down to its kept tags; count anything else."""
    records: list[dict[str, str]] = []
    other = 0
    for value in values:
        if _DMARC.match(value.strip()):
            records.append(dmarc_tags(value))
        else:
            other += 1
    return DmarcTxt(tuple(records), other)


def dmarc_tags(record: str) -> dict[str, str]:
    """The `v`, `p` and `sp` tags of one DMARC record. Every other tag is dropped unread."""
    tags: dict[str, str] = {}
    for part in record.split(";"):
        key, sep, value = part.partition("=")
        key = key.strip().lower()
        if sep and key in DMARC_KEPT_TAGS and key not in tags:
            tags[key] = value.strip()
    return tags


def spf_all(record: str) -> str | None:
    """The record's `all` mechanism as written (`-all`, `~all`, `?all`, `+all`, `all`)."""
    for term in record.split()[1:]:
        if _ALL.match(term):
            return term.lower()
    return None


def spf_allows_all(record: str) -> bool:
    """`+all` or a bare `all`: every server on the internet passes (RFC 7208 §4.6.2)."""
    mechanism = spf_all(record)
    return mechanism in {"+all", "all"}


def dmarc_policy(record: dict[str, str]) -> str | None:
    policy = record.get("p")
    return policy.strip().lower() if policy else None


def dmarc_text(record: dict[str, str]) -> str:
    """The kept tags written back as a record, for evidence. Nothing else is recoverable."""
    return "; ".join(f"{key}={record[key]}" for key in DMARC_KEPT_TAGS if key in record)
