"""Structured data on the homepage — JSON-LD, microdata and RDFa — read as a search engine would.

Until v0.12.0 the audit asked one question of a page: does some JSON-LD node carry a
LocalBusiness `@type`. Four quite different pages all answered "no" and were told the
same thing, and two of those answers were false statements about the business:

* a page carrying LocalBusiness as **microdata** (`itemscope` / `itemtype`) has it;
* a page whose LocalBusiness JSON-LD **does not parse** has tried and broken it, which is
  a different — and more useful — thing to tell its owner than "you have none";
* a page with only `Organization` or `WebSite` has structured data, just not the local kind.

So this module reads all three syntaxes and reports what it found without judging it:
every type it saw, every LocalBusiness-family node with the fields a listing is compared
against, and every JSON-LD block that did not parse. `checks.py` turns that into checks and
`findings.py` decides what is worth saying.

A block is only called broken after the wrappers real CMSes put around JSON-LD — an HTML
comment, a `CDATA` section, a byte-order mark, a raw newline inside a string — have been
forgiven, because search engines forgive them too. Calling a block broken that Google reads
fine would be exactly the kind of false finding this spec exists to remove.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.modules.audit_web.fingerprints import (
    LOCAL_BUSINESS_TYPES,
    schema_type_name,
    schema_type_names,
)

# The most of one field value, and the most items of one list field, that is kept.
FIELD_MAX_CHARS = 200
FIELD_MAX_ITEMS = 10
# How many distinct types are remembered for a page. Enough to name them in a finding.
MAX_TYPES = 20

_LEADING_WRAPPERS = re.compile(
    r"^(?:\s*(?:<!--|//\s*<!\[CDATA\[|/\*\s*<!\[CDATA\[\s*\*/|<!\[CDATA\[))+"
)
_TRAILING_WRAPPERS = re.compile(r"(?:(?:-->|//\s*\]\]>|/\*\s*\]\]>\s*\*/|\]\]>)\s*)+$")
# A `"@type": "Plumber"` inside a block that does not parse: a hint, never a parse.
_TYPE_HINT = re.compile(r'"@type"\s*:\s*"([^"]{1,100})"')

MICRODATA = "microdata"
RDFA = "rdfa"
JSON_LD = "json-ld"


@dataclass(frozen=True)
class LocalBusinessNode:
    """One LocalBusiness-family item, the syntax it was written in and what it says."""

    type: str
    syntax: str
    fields: dict[str, Any]
    evidence: str


@dataclass(frozen=True)
class BrokenBlock:
    """A JSON-LD block that does not parse, verbatim, and the type it appears to declare."""

    raw: str
    type_hint: str | None
    error: str


@dataclass
class StructuredData:
    types: list[str] = field(default_factory=list)
    local_businesses: list[LocalBusinessNode] = field(default_factory=list)
    broken_blocks: list[BrokenBlock] = field(default_factory=list)

    def add_types(self, names: list[str]) -> None:
        for name in names:
            if name not in self.types and len(self.types) < MAX_TYPES:
                self.types.append(name)


def read(soup: BeautifulSoup) -> StructuredData:
    """Everything the page declares, in the order JSON-LD, microdata, RDFa."""
    found = StructuredData()
    _read_json_ld(soup, found)
    _read_scoped(soup, found, syntax=MICRODATA)
    _read_scoped(soup, found, syntax=RDFA)
    return found


def is_local_business(name: str) -> bool:
    return name.lower() in LOCAL_BUSINESS_TYPES


# --- JSON-LD ----------------------------------------------------------------------------


def _read_json_ld(soup: BeautifulSoup, found: StructuredData) -> None:
    for script in soup.find_all("script"):
        if not isinstance(script, Tag) or not _is_json_ld(script):
            continue
        raw = script.string or script.get_text()
        text = unwrap_json_ld(raw or "")
        if not text:
            continue
        try:
            parsed = json.loads(text, strict=False)
        except (ValueError, TypeError) as exc:
            hint = _TYPE_HINT.search(text)
            found.broken_blocks.append(
                BrokenBlock(
                    raw=" ".join(text.split()),
                    type_hint=schema_type_name(hint.group(1)) if hint else None,
                    error=str(exc),
                )
            )
            continue
        for node in json_ld_nodes(parsed):
            names = schema_type_names(node.get("@type"))
            found.add_types(names)
            local = next((name for name in names if is_local_business(name)), None)
            if local is not None:
                found.local_businesses.append(
                    LocalBusinessNode(
                        type=local,
                        syntax=JSON_LD,
                        fields=_fields(node),
                        evidence=" ".join(text.split()),
                    )
                )


def _is_json_ld(script: Tag) -> bool:
    kind = str(script.get("type") or "").split(";", 1)[0].strip().lower()
    return kind == "application/ld+json"


def unwrap_json_ld(raw: str) -> str:
    """The block without the wrappers CMSes add: a BOM, HTML comments, `CDATA` markers."""
    text = raw.strip().lstrip("﻿").strip()
    text = _LEADING_WRAPPERS.sub("", text)
    text = _TRAILING_WRAPPERS.sub("", text)
    return text.strip()


def json_ld_nodes(parsed: object) -> list[dict[str, Any]]:
    """Flatten a JSON-LD document, including `@graph` and top-level arrays."""
    nodes: list[dict[str, Any]] = []
    stack: list[object] = [parsed]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            nodes.append(current)
            graph = current.get("@graph")
            if graph is not None:
                stack.append(graph)
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return nodes


def _fields(node: dict[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "name": _text(node.get("name")),
            "telephone": _text(node.get("telephone")),
            "address": _address(node.get("address")),
            "same_as": _strings(node.get("sameAs")),
            "opening_hours": _strings(node.get("openingHours"))
            or _hours_specification(node.get("openingHoursSpecification")),
        }
    )


# --- microdata and RDFa -------------------------------------------------------------------

# How each attribute-based syntax spells "an item starts here", its type, and a property.
_SYNTAX_ATTRIBUTES = {
    MICRODATA: ("itemscope", "itemtype", "itemprop"),
    RDFA: ("typeof", "typeof", "property"),
}


def _read_scoped(soup: BeautifulSoup, found: StructuredData, *, syntax: str) -> None:
    scope_attr, type_attr, _ = _SYNTAX_ATTRIBUTES[syntax]
    for element in soup.find_all(attrs={scope_attr: True}):
        if not isinstance(element, Tag) or not element.get(type_attr):
            continue
        names = schema_type_names(str(_attr(element, type_attr)))
        found.add_types(names)
        local = next((name for name in names if is_local_business(name)), None)
        if local is None:
            continue
        properties = _properties(element, syntax=syntax)
        found.local_businesses.append(
            LocalBusinessNode(
                type=local,
                syntax=syntax,
                fields=_compact(
                    {
                        "name": _first_text(properties.get("name")),
                        "telephone": _first_text(properties.get("telephone")),
                        "address": _address(_first(properties.get("address"))),
                        "same_as": _strings(properties.get("sameAs")),
                        "opening_hours": _strings(properties.get("openingHours")),
                    }
                ),
                evidence=_opening_tag(element),
            )
        )


def _properties(scope: Tag, *, syntax: str) -> dict[str, list[Any]]:
    """The properties that belong to `scope` itself, not to an item nested inside it."""
    scope_attr, _, prop_attr = _SYNTAX_ATTRIBUTES[syntax]
    properties: dict[str, list[Any]] = {}
    for tag in scope.find_all(attrs={prop_attr: True}):
        if not isinstance(tag, Tag) or _owner(tag, scope_attr) is not scope:
            continue
        nested = tag.get(scope_attr) is not None and tag is not scope
        value: Any = _nested_properties(tag, syntax=syntax) if nested else _property_value(tag)
        for raw_name in str(_attr(tag, prop_attr)).split():
            name = schema_type_name(raw_name)
            if name is not None:
                properties.setdefault(name, []).append(value)
    return properties


def _nested_properties(tag: Tag, *, syntax: str) -> dict[str, Any]:
    """A nested item (a PostalAddress, say) as a JSON-LD-shaped dict of its first values."""
    return {name: values[0] for name, values in _properties(tag, syntax=syntax).items() if values}


def _owner(tag: Tag, scope_attr: str) -> Tag | None:
    """The nearest item scope *above* `tag` — the item a property belongs to."""
    parent = tag.parent
    while isinstance(parent, Tag):
        if parent.get(scope_attr) is not None:
            return parent
        parent = parent.parent
    return None


def _property_value(tag: Tag) -> str | None:
    """A property's value, read from the attribute the HTML spec says carries it."""
    if tag.get("content") is not None:
        return _attr(tag, "content")
    by_tag: dict[str, str] = {
        "a": "href",
        "link": "href",
        "area": "href",
        "img": "src",
        "audio": "src",
        "video": "src",
        "source": "src",
        "iframe": "src",
        "embed": "src",
        "object": "data",
        "time": "datetime",
        "data": "value",
        "meter": "value",
    }
    source = by_tag.get(tag.name or "")
    if source is not None and tag.get(source) is not None:
        return _attr(tag, source)
    text = tag.get_text(" ", strip=True)
    return " ".join(text.split()) or None


def _opening_tag(element: Tag) -> str:
    return str(element).split(">", 1)[0] + ">"


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return str(value).strip() if value is not None else None


# --- field shaping ------------------------------------------------------------------------


def _address(raw: object) -> dict[str, str] | None:
    """A PostalAddress as `street / locality / region / postal_code / country`, or its text."""
    value = raw[0] if isinstance(raw, list) and raw else raw
    if isinstance(value, str):
        text = _clip(value)
        return {"text": text} if text else None
    if not isinstance(value, dict):
        return None
    country = value.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    shaped = _compact(
        {
            "street": _text(value.get("streetAddress")),
            "locality": _text(value.get("addressLocality")),
            "region": _text(value.get("addressRegion")),
            "postal_code": _text(value.get("postalCode")),
            "country": _text(country),
        }
    )
    return shaped or None


def _hours_specification(raw: object) -> list[str] | None:
    """`openingHoursSpecification` as short lines: `Monday 08:00-17:00`."""
    items = raw if isinstance(raw, list) else [raw]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        days = item.get("dayOfWeek")
        day_names = [schema_type_name(day) for day in (days if isinstance(days, list) else [days])]
        opens, closes = _text(item.get("opens")), _text(item.get("closes"))
        label = ", ".join(day for day in day_names if day)
        line = " ".join(part for part in (label, f"{opens or '?'}-{closes or '?'}") if part)
        lines.append(line)
        if len(lines) >= FIELD_MAX_ITEMS:
            break
    return lines or None


def _text(raw: object) -> str | None:
    if isinstance(raw, list):
        return next((text for text in (_text(item) for item in raw) if text), None)
    if isinstance(raw, dict):
        return _text(raw.get("@value") or raw.get("name"))
    if isinstance(raw, str | int | float) and not isinstance(raw, bool):
        return _clip(str(raw))
    return None


def _first(values: list[Any] | None) -> Any:
    return values[0] if values else None


def _first_text(values: list[Any] | None) -> str | None:
    return _text(_first(values))


def _strings(raw: object) -> list[str] | None:
    items = raw if isinstance(raw, list) else [raw]
    texts = [text for text in (_text(item) for item in items) if text]
    return texts[:FIELD_MAX_ITEMS] or None


def _clip(text: str) -> str | None:
    collapsed = " ".join(text.split())
    return collapsed[:FIELD_MAX_CHARS] or None


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, [], {})}
