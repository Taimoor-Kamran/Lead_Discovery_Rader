"""The deterministic half of an audit: what the homepage actually says (blueprint slide 34).

Every check answers one question about one page and returns three things: the value, the
verbatim text it read that value from, and the URL it read it at. That triple is what
turns a finding from an opinion into something a salesperson can point at.

Three rules hold throughout:

* **Nothing is guessed.** A check that cannot tell returns `None`, never a default. A page
  we were not allowed to parse produces no checks at all rather than empty ones.
* **`null` and `false` mean different things.** On a page that was fetched and parsed, a
  thing that is not there is `False` — the audit looked and it was absent. `None` is kept
  for the other case: the check could not run at all (nothing was parsed, or the question
  does not apply, such as a certificate on a page served over http). Reading an audit, a
  person must never have to wonder which of the two a `null` meant.
* **Nothing is harvested.** Where the spec asks for contact options, only their *presence*
  and a single example are recorded — never a list of addresses or numbers.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from app.core.safe_fetch import FetchOutcome
from app.modules.audit_web.fingerprints import (
    BOOKING_SIGNATURES,
    CART_LINK_PATTERNS,
    ECOMMERCE_SIGNATURES,
    SOCIAL_PLATFORMS,
    TECH_SIGNATURES,
    booking_text_match,
    find_signatures,
    generator_label,
    is_local_business_type,
    snippet_around,
)

EVIDENCE_MAX_CHARS = 300
# What `tls_valid` says about a page that was never served over https.
NOT_APPLICABLE_OVER_HTTP = "not applicable: served over http"
# The earliest copyright year worth believing; anything older is a typo or a date in prose.
EARLIEST_COPYRIGHT_YEAR = 1995
JS_SHELL_TEXT_CHARS = 200
JS_SHELL_SCRIPT_TAGS = 5
CONTACT_INPUT_HINTS = ("email", "e-mail", "mail", "phone", "tel", "mobile")
COPYRIGHT_PATTERN = re.compile(r"(?:©|&copy;|\(c\)|copyright)[^0-9]{0,40}(\d{4})", re.IGNORECASE)
# A range such as "© 2018-2024" (with any of the three dashes): the later year is the one
# that matters. `\u2013` and `\u2014` are written escaped so the source stays ASCII.
COPYRIGHT_RANGE_PATTERN = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)[^0-9]{0,40}\d{4}\s*[-\u2013\u2014]\s*(\d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CheckResult:
    """One observation, with the text and the URL it came from."""

    value: Any
    evidence_text: str | None = None
    evidence_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "evidence_text": clip(self.evidence_text),
            "evidence_url": self.evidence_url,
        }


Checks = dict[str, CheckResult]


def find_tags(node: BeautifulSoup | Tag, *args: Any, **kwargs: Any) -> list[Tag]:
    """`find_all`, narrowed to real tags. BeautifulSoup's own return type is untyped."""
    return [tag for tag in node.find_all(*args, **kwargs) if isinstance(tag, Tag)]


def clip(text: str | None) -> str | None:
    """Collapse whitespace and cut evidence to its limit. Verbatim otherwise."""
    if text is None:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    return collapsed[:EVIDENCE_MAX_CHARS]


def as_payload(checks: Checks) -> dict[str, Any]:
    return {key: result.as_dict() for key, result in checks.items()}


def value_of(checks: Checks, key: str) -> Any:
    result = checks.get(key)
    return result.value if result is not None else None


# --- from the fetch itself -------------------------------------------------------------


def fetch_checks(outcome: FetchOutcome) -> Checks:
    """Everything knowable without parsing: reachability, the URL chain, HTTPS and TLS."""
    final_url = outcome.final_url or outcome.url
    scheme = urlsplit(final_url).scheme.lower()
    over_https = scheme == "https"
    checks: Checks = {
        "reachable": CheckResult(
            outcome.reachable,
            evidence_text=(
                f"HTTP {outcome.status_code} from {final_url}"
                if outcome.reachable
                else f"No HTTP answer from {final_url}: {outcome.error}"
            ),
            evidence_url=final_url,
        ),
        "http_status": CheckResult(
            outcome.status_code,
            evidence_text=(
                None if outcome.status_code is None else f"HTTP {outcome.status_code} {final_url}"
            ),
            evidence_url=final_url,
        ),
        "final_url": CheckResult(
            final_url, evidence_text=f"Requested {outcome.url}", evidence_url=final_url
        ),
        "redirect_chain": CheckResult(
            list(outcome.redirect_chain),
            evidence_text=(
                " -> ".join([*outcome.redirect_chain, final_url])
                if outcome.redirect_chain
                else None
            ),
            evidence_url=final_url,
        ),
        # There is no certificate to judge on a page served over plain http, so the
        # answer is `None` rather than `True`: saying a certificate verified when none was
        # ever presented would be inventing the one fact this check exists to establish.
        "tls_valid": CheckResult(
            outcome.tls_valid if over_https else None,
            evidence_text=(
                NOT_APPLICABLE_OVER_HTTP
                if not over_https
                else (
                    f"The certificate for {final_url} verified"
                    if outcome.tls_valid
                    else f"The certificate for {final_url} did not verify: {outcome.error}"
                )
            ),
            evidence_url=final_url,
        ),
        "truncated": CheckResult(
            outcome.truncated,
            evidence_text=(
                f"The response body was cut off at the size cap: {final_url}"
                if outcome.truncated
                else None
            ),
            evidence_url=final_url,
        ),
        "content_type": CheckResult(
            outcome.content_type,
            evidence_text=outcome.content_type,
            evidence_url=final_url,
        ),
        "parsed": CheckResult(
            outcome.parsed,
            evidence_text=(
                None
                if outcome.parsed
                else f"Content type {outcome.content_type or 'unknown'} is not parsed as HTML"
            ),
            evidence_url=final_url,
        ),
    }

    started_http = urlsplit(outcome.url).scheme.lower() == "http"
    checks["https"] = CheckResult(
        over_https if outcome.reachable or outcome.error_kind == "tls" else None,
        evidence_text=f"The final URL is {final_url}",
        evidence_url=final_url,
    )
    checks["http_redirects_to_https"] = CheckResult(
        over_https if started_http and outcome.reachable else None,
        evidence_text=(
            f"{outcome.url} ended on {final_url}" if started_http and outcome.reachable else None
        ),
        evidence_url=final_url,
    )
    return checks


# --- from the HTML ---------------------------------------------------------------------


def analyse_html(
    outcome: FetchOutcome, *, now: datetime | None = None, page_text_limit: int | None = None
) -> tuple[Checks, str | None]:
    """Parse the page once and return its checks plus the visible text to store.

    One parse, one text extraction: every check below reads the same tree, which is what
    keeps auditing a run of a thousand businesses proportional to the pages, not to the
    number of questions asked about each.
    """
    if outcome.text is None:
        return {}, None
    url = outcome.final_url or outcome.url
    soup = BeautifulSoup(outcome.text, "lxml")
    lowered = outcome.text.lower()
    moment = now or datetime.now(UTC)
    text = visible_text(soup)

    checks: Checks = {}
    checks.update(_meta_checks(soup, url))
    checks.update(_content_checks(soup, url, text))
    checks.update(_contact_checks(soup, url))
    checks["booking"] = _booking(soup, lowered, url)
    checks["ecommerce"] = _ecommerce(soup, lowered, url)
    checks["social_links"] = _social_links(soup, url)
    checks["structured_data"] = _structured_data(soup, url)
    checks["tech_stack"] = _tech_stack(soup, lowered, url)
    checks["copyright_year"] = _copyright_year(outcome.text, url, moment)
    checks["js_shell_suspected"] = _js_shell(soup, url, text)
    stored = text[:page_text_limit] or None if page_text_limit else None
    return checks, stored


def html_checks(outcome: FetchOutcome, *, now: datetime | None = None) -> Checks:
    """Every check that needs the page body. Empty when there was nothing parseable."""
    return analyse_html(outcome, now=now)[0]


def _meta_checks(soup: BeautifulSoup, url: str) -> Checks:
    viewport = _meta_tag(soup, "viewport")
    description = _meta_tag(soup, "description")
    title_tag = soup.title
    title = title_tag.get_text(strip=True) if title_tag is not None else None
    description_text = _attr(description, "content")

    # Each of these carries what the page said when the tag is there, and `False` when it
    # is not: the page was parsed, so "absent" is an answer, not a gap in our knowledge.
    return {
        "viewport_meta": CheckResult(
            (_attr(viewport, "content") if viewport is not None else None) or False,
            evidence_text=str(viewport) if viewport is not None else 'No <meta name="viewport">',
            evidence_url=url,
        ),
        "title": CheckResult(
            title or False,
            evidence_text=str(title_tag) if title_tag is not None else "No <title>",
            evidence_url=url,
        ),
        "title_length": CheckResult(len(title) if title else 0, evidence_url=url),
        "meta_description": CheckResult(
            description_text or False,
            evidence_text=(
                str(description) if description is not None else 'No <meta name="description">'
            ),
            evidence_url=url,
        ),
        "meta_description_length": CheckResult(
            len(description_text) if description_text else 0, evidence_url=url
        ),
        "favicon": CheckResult(
            _favicon(soup) is not None,
            evidence_text=(
                str(_favicon(soup)) if _favicon(soup) is not None else 'No <link rel="icon">'
            ),
            evidence_url=url,
        ),
    }


def _content_checks(soup: BeautifulSoup, url: str, text: str) -> Checks:
    headings = [tag for tag in find_tags(soup, "h1") if tag.get_text(strip=True)]
    first = headings[0] if headings else None
    return {
        "h1_present": CheckResult(
            bool(headings),
            evidence_text=str(first) if first is not None else "No <h1> with text",
            evidence_url=url,
        ),
        "visible_text_length": CheckResult(len(text), evidence_url=url),
    }


def _contact_checks(soup: BeautifulSoup, url: str) -> Checks:
    """Presence plus one example. Never a list — this system does no outreach."""
    tel = _first_href(soup, "tel:")
    mailto = _first_href(soup, "mailto:")
    form = _contact_form(soup)
    return {
        "tel_link": CheckResult(
            tel is not None,
            evidence_text=str(tel) if tel is not None else "No tel: link on the homepage",
            evidence_url=url,
        ),
        "mailto_link": CheckResult(
            mailto is not None,
            evidence_text=(
                str(mailto) if mailto is not None else "No mailto: link on the homepage"
            ),
            evidence_url=url,
        ),
        "contact_form": CheckResult(
            form is not None,
            evidence_text=(
                _form_evidence(form) if form is not None else "No form with a contact field"
            ),
            evidence_url=url,
        ),
    }


def _booking(soup: BeautifulSoup, lowered: str, url: str) -> CheckResult:
    hits = find_signatures(lowered, BOOKING_SIGNATURES)
    if hits:
        return CheckResult(hits[0].label, evidence_text=hits[0].evidence, evidence_url=url)
    for link in find_tags(soup, ["a", "button"]):
        label = link.get_text(" ", strip=True)
        match = booking_text_match(label.lower())
        if match is not None:
            return CheckResult(f"link text: {label}", evidence_text=str(link), evidence_url=url)
    return CheckResult(
        False,
        evidence_text="No known booking widget or 'book online' link on the homepage",
        evidence_url=url,
    )


def _ecommerce(soup: BeautifulSoup, lowered: str, url: str) -> CheckResult:
    hits = find_signatures(lowered, ECOMMERCE_SIGNATURES)
    if hits:
        return CheckResult(hits[0].label, evidence_text=hits[0].evidence, evidence_url=url)
    for link in find_tags(soup, "a", href=True):
        href = str(link["href"]).lower()
        for pattern in CART_LINK_PATTERNS:
            if pattern in href:
                return CheckResult("cart link", evidence_text=str(link), evidence_url=url)
    return CheckResult(
        False, evidence_text="No shop platform signature or cart link", evidence_url=url
    )


def _social_links(soup: BeautifulSoup, url: str) -> CheckResult:
    """Which platforms the homepage links to. The links are recorded, never followed."""
    found: dict[str, str] = {}
    for link in find_tags(soup, "a", href=True):
        href = str(link["href"])
        lowered = href.lower()
        for platform in SOCIAL_PLATFORMS:
            if platform.key in found:
                continue
            if any(pattern in lowered for pattern in platform.patterns):
                found[platform.key] = href
    return CheckResult(
        sorted(found),
        evidence_text=(
            "; ".join(f"{key}: {href}" for key, href in sorted(found.items()))
            if found
            else "No social profile links on the homepage"
        ),
        evidence_url=url,
    )


def _structured_data(soup: BeautifulSoup, url: str) -> CheckResult:
    """Whether the page carries LocalBusiness JSON-LD, and the block that says so."""
    for script in find_tags(soup, "script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for node in _json_ld_nodes(parsed):
            if is_local_business_type(node.get("@type")):
                return CheckResult(
                    str(node.get("@type")),
                    evidence_text=" ".join(raw.split()),
                    evidence_url=url,
                )
    return CheckResult(
        False, evidence_text="No LocalBusiness JSON-LD block on the homepage", evidence_url=url
    )


def _json_ld_nodes(parsed: object) -> list[dict[str, Any]]:
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
            stack.extend(current)
    return nodes


def _tech_stack(soup: BeautifulSoup, lowered: str, url: str) -> CheckResult:
    generator_tag = _meta_tag(soup, "generator")
    generator = _attr(generator_tag, "content")
    platforms: list[str] = []
    evidence: list[str] = []

    if generator:
        label = generator_label(generator)
        if label is not None:
            platforms.append(label)
        evidence.append(str(generator_tag))

    for hit in find_signatures(lowered, TECH_SIGNATURES):
        if hit.label not in platforms:
            platforms.append(hit.label)
            evidence.append(hit.pattern)

    return CheckResult(
        {"generator": generator or None, "platforms": platforms},
        evidence_text="; ".join(evidence) if evidence else "No platform signature on the homepage",
        evidence_url=url,
    )


def _copyright_year(html: str, url: str, now: datetime) -> CheckResult:
    """The highest believable year printed next to a copyright mark."""
    years: list[tuple[int, str]] = []
    for pattern in (COPYRIGHT_RANGE_PATTERN, COPYRIGHT_PATTERN):
        for match in pattern.finditer(html):
            year = int(match.group(1))
            if EARLIEST_COPYRIGHT_YEAR <= year <= now.year:
                years.append((year, snippet_around(html, match.start(), len(match.group(0)))))
    if not years:
        return CheckResult(
            False, evidence_text="No copyright year on the homepage", evidence_url=url
        )
    best = max(years, key=lambda item: item[0])
    return CheckResult(best[0], evidence_text=best[1], evidence_url=url)


def _js_shell(soup: BeautifulSoup, url: str, text: str) -> CheckResult:
    """A page whose content is assembled by JavaScript we deliberately do not run."""
    scripts = find_tags(soup, "script")
    suspected = len(text) < JS_SHELL_TEXT_CHARS and len(scripts) >= JS_SHELL_SCRIPT_TAGS
    return CheckResult(
        suspected,
        evidence_text=(
            f"The homepage carries {len(scripts)} script tags and only {len(text)} characters "
            "of visible text"
        ),
        evidence_url=url,
    )


# --- small helpers ---------------------------------------------------------------------


def visible_text(soup: BeautifulSoup) -> str:
    """The text a person would read: no scripts, styles, templates or comments."""
    clone = BeautifulSoup(str(soup), "lxml")
    for tag in find_tags(clone, ["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body = clone.body or clone
    return " ".join(body.get_text(" ", strip=True).split())


def page_text(outcome: FetchOutcome, *, limit: int) -> str | None:
    """The visible text an audit stores, capped. Input for the v0.5.0 AI step.

    A convenience for tests and callers that want only the text; the audit itself gets it
    from `analyse_html`, which does the one parse it needs.
    """
    return analyse_html(outcome, page_text_limit=limit)[1]


def _meta_tag(soup: BeautifulSoup, name: str) -> Tag | None:
    for tag in find_tags(soup, "meta"):
        if str(tag.get("name", "")).strip().lower() == name:
            return tag
    return None


def _attr(tag: Tag | None, name: str) -> str | None:
    if tag is None:
        return None
    value = tag.get(name)
    if isinstance(value, list):
        value = " ".join(value)
    return str(value).strip() if value is not None else None


def _favicon(soup: BeautifulSoup) -> Tag | None:
    for tag in find_tags(soup, "link"):
        rel = tag.get("rel") or []
        tokens = {str(item).lower() for item in (rel if isinstance(rel, list) else [rel])}
        if tokens & {"icon", "shortcut icon", "apple-touch-icon"}:
            return tag
    return None


def _first_href(soup: BeautifulSoup, prefix: str) -> Tag | None:
    for tag in find_tags(soup, "a", href=True):
        if str(tag["href"]).strip().lower().startswith(prefix):
            return tag
    return None


def _contact_form(soup: BeautifulSoup) -> Tag | None:
    """A form carrying an email or phone field — the third way to reach a business."""
    for form in find_tags(soup, "form"):
        for field in find_tags(form, ["input", "textarea"]):
            haystack = " ".join(
                str(field.get(attribute, "")).lower()
                for attribute in ("type", "name", "id", "placeholder", "autocomplete")
            )
            if any(hint in haystack for hint in CONTACT_INPUT_HINTS):
                return form
    return None


def _form_evidence(form: Tag) -> str:
    """The form's opening tag plus its field names, which is what makes it a contact form."""
    names = [
        str(field.get("name") or field.get("type") or "") for field in find_tags(form, "input")
    ]
    opening = str(form).split(">", 1)[0] + ">"
    return f"{opening} fields: {', '.join(name for name in names if name)}"
