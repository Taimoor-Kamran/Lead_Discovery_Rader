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
  and a single example are recorded — never a list of addresses. The exceptions are the
  business's own published contact points that a listing comparison needs (v0.12.0): at
  most three `tel:` numbers, and the one `mailto:` address that is quoted as evidence.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from app.core.safe_fetch import FetchOutcome
from app.modules.audit_web import structured_data
from app.modules.audit_web.fingerprints import (
    AMBIGUOUS_EMAIL_DOMAINS,
    BOOKING_HREF_SEGMENTS,
    BOOKING_SIGNATURES,
    BOT_CHALLENGE_MARKERS,
    BOT_CHALLENGE_STATUSES,
    BOT_CHALLENGE_TITLES,
    BUILDER_LABELS,
    BUILDER_SIGNATURES,
    CHAT_SIGNATURES,
    FOOTER_PLACEHOLDER_PHRASES,
    PAGE_PLACEHOLDER_PHRASES,
    PLACEHOLDER_EMAIL_DOMAINS,
    PLACEHOLDER_EMAIL_LABELS,
    PLACEHOLDER_LOCAL_PARTS,
    SOCIAL_PLATFORMS,
    TECH_SIGNATURES,
    booking_host,
    booking_text_match,
    find_signatures,
    generator_label,
    is_never_booking_host,
    is_social_host,
    snippet_forward,
    trim_to_words,
)

EVIDENCE_MAX_CHARS = 300
# What `tls_valid` says about a page that was never served over https.
NOT_APPLICABLE_OVER_HTTP = "not applicable: served over http"
# The earliest copyright year worth believing; anything older is a typo or a date in prose.
EARLIEST_COPYRIGHT_YEAR = 1995
# How much of the line a copyright notice sits on is kept as its evidence.
COPYRIGHT_EVIDENCE_CHARS = 160
JS_SHELL_TEXT_CHARS = 200
JS_SHELL_SCRIPT_TAGS = 5
CONTACT_INPUT_HINTS = ("email", "e-mail", "mail", "phone", "tel", "mobile")
# A year is exactly four digits: "© 20015" (a typo on a real site) is no year, never 2001.
COPYRIGHT_PATTERN = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)[^0-9]{0,40}(\d{4})(?!\d)", re.IGNORECASE
)
# A range such as "© 2018-2024" (with any of the three dashes): the later year is the one
# that matters. `\u2013` and `\u2014` are written escaped so the source stays ASCII.
COPYRIGHT_RANGE_PATTERN = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)[^0-9]{0,40}\d{4}\s*[-\u2013\u2014]\s*(\d{4})(?!\d)",
    re.IGNORECASE,
)
# What follows a year that opens a range with no end year: "© Copyright 2006 - | …". The
# end year is usually written by a script, which this audit does not run (v0.12.0, run 4).
OPEN_RANGE_TAIL = re.compile(r"\s*[-\u2013\u2014](?!\s*\d)")
# Said before every copyright year quoted as evidence.
AS_IN_SOURCE = "As written in the page's HTML source (a year a script fills in is not seen): "
# A year printed *directly* after the mark — "© 2035", "Copyright © 2018-2035" — and the
# range it may end. Only a year in this position is believed when it lies in the future:
# the looser patterns above allow forty characters of text before the year, and in
# "© Acme, 2100 Lamar Blvd" that is a street number, not a year (v0.12.0, item 2).
COPYRIGHT_ADJACENT_PATTERN = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)(?:\s*(?:©|&copy;|\(c\)))*\s*(\d{4})"
    r"(?:\s*[-\u2013\u2014]\s*(\d{4}))?(?!\d)",
    re.IGNORECASE,
)
# How far ahead a printed year may be and still be read as a year. Further than this it is
# more likely a number that happens to follow a copyright mark.
FUTURE_COPYRIGHT_MAX_YEARS = 30
# Most `tel:` numbers kept for the listing comparison.
MAX_TEL_NUMBERS = 3
# The longest piece of footer text a placeholder phrase is looked for in, and how much of
# the text after a copyright mark counts as its line (spec v0.12.0, canary fix).
PLACEHOLDER_TEXT_MAX_CHARS = 60
COPYRIGHT_LINE_CHARS = 80
# Zoom is treated as blocked below this `maximum-scale` (spec v0.12.0, item 5).
MIN_MAXIMUM_SCALE = 1.5


@dataclass(frozen=True)
class CheckResult:
    """One observation, with the text and the URL it came from."""

    value: Any
    evidence_text: str | None = None
    evidence_url: str | None = None
    # How much evidence is kept. 300 characters for almost everything; more only where a
    # person could not otherwise verify the claim (a broken JSON-LD block, v0.12.0).
    evidence_max: int = 300
    # How the value was arrived at, and the page it points to, where that matters (only
    # `booking` since v0.12.1). Stored only when set, so every other check is unchanged.
    method: str | None = None
    target_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        stored: dict[str, Any] = {
            "value": self.value,
            "evidence_text": clip(self.evidence_text, self.evidence_max),
            "evidence_url": self.evidence_url,
        }
        if self.method is not None:
            stored["method"] = self.method
        if self.target_url is not None:
            stored["target_url"] = self.target_url
        return stored


Checks = dict[str, CheckResult]


def find_tags(node: BeautifulSoup | Tag, *args: Any, **kwargs: Any) -> list[Tag]:
    """`find_all`, narrowed to real tags. BeautifulSoup's own return type is untyped."""
    return [tag for tag in node.find_all(*args, **kwargs) if isinstance(tag, Tag)]


def clip(text: str | None, limit: int = EVIDENCE_MAX_CHARS) -> str | None:
    """Collapse whitespace and cut evidence to its limit. Verbatim otherwise."""
    if text is None:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    return collapsed[:limit]


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


def bot_challenge(outcome: FetchOutcome) -> CheckResult | None:
    """A bot-protection challenge served instead of the homepage, or `None` (item 3b).

    Needs both a challenge status (403, 429, 503) and a vendor's own mark — its challenge
    title or a marker in the body. Either alone is not enough: a plain 403 is a site
    refusing us for its own reasons, and Cloudflare puts its challenge-platform script on
    ordinary 200 homepages too.
    """
    if outcome.status_code not in BOT_CHALLENGE_STATUSES or outcome.text is None:
        return None
    url = outcome.final_url or outcome.url
    lowered = outcome.text.lower()
    title_tag = BeautifulSoup(outcome.text, "lxml").title
    title = " ".join(title_tag.get_text().split()).lower() if title_tag is not None else ""
    vendor = next((name for name, known in BOT_CHALLENGE_TITLES if title == known), None)
    if vendor is None:
        vendor = next((name for name, marker in BOT_CHALLENGE_MARKERS if marker in lowered), None)
    if vendor is None:
        return None
    shown = str(title_tag) if title_tag is not None else "no <title>"
    return CheckResult(
        vendor,
        evidence_text=f"HTTP {outcome.status_code} with a {vendor} bot-protection page: {shown}",
        evidence_url=url,
    )


def not_readable(outcome: FetchOutcome) -> CheckResult | None:
    """A non-2xx answer, which is never read as the homepage (v0.12.0, run 2), or `None`.

    Only the status code and the response's `<title>` are read — as evidence of what came
    back, never as facts about the business's page.
    """
    status = outcome.status_code
    if status is None or 200 <= status < 300:
        return None
    url = outcome.final_url or outcome.url
    title = None
    if outcome.text:
        tag = BeautifulSoup(outcome.text, "lxml").title
        title = str(tag) if tag is not None else None
    return CheckResult(
        status,
        evidence_text=f"HTTP {status} from {url}: {title or 'no <title>'}",
        evidence_url=url,
    )


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
    cleaned = _cleaned(soup)
    text = _text_of(cleaned.body or cleaned)

    checks: Checks = {}
    checks.update(_meta_checks(soup, url))
    checks.update(_content_checks(soup, url, text))
    checks.update(_contact_checks(soup, url))
    checks["booking"] = _booking(soup, lowered, url)
    checks["live_chat"] = _live_chat(lowered, url)
    checks["social_links"] = _social_links(soup, url)
    checks.update(_structured_data(soup, url))
    checks["tech_stack"] = _tech_stack(soup, lowered, url)
    checks["copyright_year"] = _copyright_year(text, url, moment)
    checks["placeholder_text"] = _placeholder_text(cleaned, text, url)
    checks["js_shell_suspected"] = _js_shell(soup, url, text)
    checks["images_without_alt"] = _images_without_alt(soup, url)
    checks["unlabelled_inputs"] = _unlabelled_inputs(soup, url)
    checks["word_count"] = _word_count(text, url)
    checks["heading_structure"] = _heading_structure(soup, url)
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
        "viewport_zoom_blocked": _viewport_zoom(viewport, url),
        "canonical_url": _canonical(soup, url),
    }


def _viewport_zoom(viewport: Tag | None, url: str) -> CheckResult:
    """Whether the viewport tag asks the browser to stop visitors zooming, and how.

    `user-scalable=no` (or `0`) and a `maximum-scale` below 1.5 each do. A value that is not
    a number is not read as one: an unreadable `maximum-scale` blocks nothing we can prove.
    """
    content = _attr(viewport, "content") if viewport is not None else None
    if not content:
        return CheckResult(
            False, evidence_text='No <meta name="viewport"> content', evidence_url=url
        )
    settings: dict[str, str] = {}
    for part in re.split(r"[,;]", content):
        key, _, value = part.partition("=")
        if key.strip():
            settings[key.strip().lower()] = value.strip().lower()
    reasons: list[str] = []
    if settings.get("user-scalable") in ("no", "0"):
        reasons.append(f"user-scalable={settings['user-scalable']}")
    maximum = settings.get("maximum-scale")
    try:
        if maximum is not None and float(maximum) < MIN_MAXIMUM_SCALE:
            reasons.append(f"maximum-scale={maximum}")
    except ValueError:
        pass
    return CheckResult(", ".join(reasons) or False, evidence_text=str(viewport), evidence_url=url)


def _canonical(soup: BeautifulSoup, url: str) -> CheckResult:
    """The URL the page names as its own (`<link rel="canonical">`), made absolute."""
    for tag in find_tags(soup, "link"):
        rel = tag.get("rel") or []
        tokens = {str(item).lower() for item in (rel if isinstance(rel, list) else [rel])}
        href = _attr(tag, "href")
        if "canonical" in tokens and href:
            return CheckResult(urljoin(url, href), evidence_text=str(tag), evidence_url=url)
    return CheckResult(False, evidence_text='No <link rel="canonical">', evidence_url=url)


def _content_checks(soup: BeautifulSoup, url: str, text: str) -> Checks:
    headings = [tag for tag in find_tags(soup, "h1") if tag.get_text(strip=True)]
    first = headings[0] if headings else None
    return {
        "h1_present": CheckResult(
            bool(headings),
            evidence_text=str(first) if first is not None else "No <h1> with text",
            evidence_url=url,
        ),
        "h1_count": CheckResult(
            len(headings),
            evidence_text=_first_tags(headings) if headings else "No <h1> with text",
            evidence_url=url,
        ),
        "visible_text_length": CheckResult(len(text), evidence_url=url),
    }


def _contact_checks(soup: BeautifulSoup, url: str) -> Checks:
    """Presence plus one example, and what a listing comparison needs. Never a list."""
    tel = _first_href(soup, "tel:")
    mailto = _first_href(soup, "mailto:")
    form = _contact_form(soup)
    tel_tags = _hrefs(soup, "tel:")
    numbers: list[str] = []
    for tag in tel_tags:
        number = _tel_number(str(tag["href"]))
        if number and number not in numbers and len(numbers) < MAX_TEL_NUMBERS:
            numbers.append(number)
    return {
        "tel_link": CheckResult(
            tel is not None,
            evidence_text=str(tel) if tel is not None else "No tel: link on the homepage",
            evidence_url=url,
        ),
        "tel_numbers": CheckResult(
            numbers,
            evidence_text=_first_tags(tel_tags) if tel_tags else "No tel: link on the homepage",
            evidence_url=url,
        ),
        "mailto_link": CheckResult(
            mailto is not None,
            evidence_text=(
                str(mailto) if mailto is not None else "No mailto: link on the homepage"
            ),
            evidence_url=url,
        ),
        "mailto_address": _mailto_address(soup, url),
        "contact_form": CheckResult(
            form is not None,
            evidence_text=(
                _form_evidence(form) if form is not None else "No form with a contact field"
            ),
            evidence_url=url,
        ),
    }


def _tel_number(href: str) -> str | None:
    """The number a `tel:` link dials, as written: `tel:+1-512-555-0100` → `+1-512-555-0100`."""
    value = unquote(href.strip()[len("tel:") :]).split(";", 1)[0].strip()
    return value or None


def _mailto_address(soup: BeautifulSoup, url: str) -> CheckResult:
    """The email address the homepage offers, and whether it is a template placeholder.

    Every `mailto:` link is looked at, so a placeholder in the footer is found even when a
    real address is linked higher up. The placeholder is what is reported when there is
    one; otherwise the first address, which is the business's own published one.
    """
    first: tuple[str, Tag] | None = None
    for tag in _hrefs(soup, "mailto:"):
        for address in _mailto_addresses(str(tag["href"])):
            if is_placeholder_email(address):
                return CheckResult(
                    {"address": address, "placeholder": True},
                    evidence_text=str(tag),
                    evidence_url=url,
                )
            if first is None:
                first = (address, tag)
    if first is None:
        return CheckResult(False, evidence_text="No mailto: link on the homepage", evidence_url=url)
    return CheckResult(
        {"address": first[0], "placeholder": False}, evidence_text=str(first[1]), evidence_url=url
    )


def _mailto_addresses(href: str) -> list[str]:
    """`mailto:a@b.com,c@d.com?subject=x` → `["a@b.com", "c@d.com"]`, lowercased."""
    target = unquote(href.strip()[len("mailto:") :].split("?", 1)[0])
    return [part.strip().lower() for part in target.split(",") if "@" in part]


def is_placeholder_email(address: str) -> bool:
    """Whether an address is at a domain no business receives mail at (see fingerprints)."""
    local, _, domain = address.strip().lower().rpartition("@")
    domain = domain.rstrip(".")
    if not local or not domain:
        return False
    if domain in PLACEHOLDER_EMAIL_DOMAINS or domain.split(".", 1)[0] in PLACEHOLDER_EMAIL_LABELS:
        return True
    return domain in AMBIGUOUS_EMAIL_DOMAINS and local in PLACEHOLDER_LOCAL_PARTS


# How `booking` was decided (v0.12.1). Only the first three are a booking flow the audit
# actually saw; `cta` and `path` are a link that *says* booking, to be followed once.
BOOKING_WIDGET = "widget"
BOOKING_HOST = "booking_host"
BOOKING_VERIFIED_TARGET = "verified_target"
BOOKING_CTA = "cta"
BOOKING_PATH = "path"
BOOKING_UNVERIFIED = "unverified"
# The target reads fine and shows no scheduler, but leads on: a booking link, a location
# picker, another host. The booking may be one hop further (Bishops: /locations/ → a
# location → Zenoti), and only one page is ever fetched, so nothing is concluded.
BOOKING_UNVERIFIED_MULTI_HOP = "unverified_multi_hop"
BOOKING_TO_FOLLOW = frozenset({BOOKING_CTA, BOOKING_PATH})
# Input types that are a date or a time, and the words in an input's name, id, class or
# placeholder that say it is one (a Gravity Forms date field is `type="text"
# class="datepicker"`).
DATE_TIME_INPUT_TYPES = frozenset({"date", "time", "datetime-local", "month", "week"})
# Words that make a link on a booking target lead on to booking (v0.12.1), matched as
# whole words of its text or href: `facebook` and `bookkeeping` are not `book`.
ONWARD_BOOKING_WORDS = frozenset(
    {
        "book",
        "booking",
        "bookings",
        "appointment",
        "appointments",
        "schedule",
        "scheduling",
        "reserve",
        "reservation",
        "reservations",
    }
)
# Words of a branch, store, location or city picker: a chain's "choose your salon" page.
LOCATION_WORDS = frozenset(
    {
        "location",
        "locations",
        "branch",
        "branches",
        "store",
        "stores",
        "salon",
        "salons",
        "city",
        "cities",
    }
)
DATE_TIME_WORD = re.compile(r"(?:^|[^a-z])(date|time|datepicker|timepicker)(?:[^a-z]|$)")


def _booking(soup: BeautifulSoup, lowered: str, url: str) -> CheckResult:
    """A booking widget, a link to a booking provider, or a link that offers to book.

    Most specific first. A widget signature names the tool. A link whose host is a known
    booking provider is a booking page whatever it says. A call to action — read from
    whatever a visitor clicks, by its text or by its `aria-label`, `title` or `value` —
    and a link whose path is a booking page are only what the link *says*: they are
    recorded with `method` `cta` or `path` and the page they point to, and the audit
    follows that one link before it concludes anything (v0.12.1).
    """
    hits = find_signatures(lowered, BOOKING_SIGNATURES)
    if hits:
        return CheckResult(
            hits[0].label, evidence_text=hits[0].evidence, evidence_url=url, method=BOOKING_WIDGET
        )
    for link in find_tags(soup, "a", href=True):
        href = str(link["href"]).strip()
        provider = booking_host(urljoin(url, href))
        if provider is not None:
            return CheckResult(
                provider,
                evidence_text=str(link),
                evidence_url=url,
                method=BOOKING_HOST,
                target_url=_link_target(url, href),
            )
    # A call to action that leads back to the page being audited is never evidence of
    # booking (ATX's "Schedule Now" on /contact/ points at /contact/): skipped without a
    # fetch, and named in the evidence if nothing else is found.
    self_links: list[str] = []
    for element in _clickables(soup):
        label = _clickable_label(element)
        if label and booking_text_match(label.lower()) is not None:
            target = _clickable_target(element, url)
            if target is not None and same_page(target, url):
                self_links.append(f"'{label}' links back to this page")
                continue
            return CheckResult(
                f"link text: {label}",
                evidence_text=str(element),
                evidence_url=url,
                method=BOOKING_CTA,
                target_url=target,
            )
    for link in find_tags(soup, "a", href=True):
        href = str(link["href"]).strip()
        if _is_booking_path(href):
            target = _link_target(url, href)
            if target is not None and same_page(target, url):
                self_links.append(f"'{href}' links back to this page")
                continue
            return CheckResult(
                f"link href: {href}",
                evidence_text=str(link),
                evidence_url=url,
                method=BOOKING_PATH,
                target_url=target,
            )
    evidence = "No known booking widget or 'book online' link on the homepage"
    if self_links:
        evidence = f"{evidence}; {self_links[0]}, which is not a booking flow"
    return CheckResult(False, evidence_text=evidence, evidence_url=url)


def _page_key(url: str) -> tuple[str, str]:
    parts = urlsplit(url.strip().lower())
    host = (parts.hostname or "").removeprefix("www.")
    return host, parts.path.rstrip("/") or "/"


def same_page(a: str, b: str) -> bool:
    """Whether two URLs are the same page: host (with or without `www.`) and path.

    The query and the fragment are ignored — `/contact/?ref=cta#form` is still `/contact/`.
    """
    return _page_key(a) == _page_key(b)


def _link_target(base: str, href: str) -> str | None:
    """The absolute http(s) page a link leads to, fragment dropped, or `None`."""
    lowered = href.strip().lower()
    if not lowered or lowered.startswith(("mailto:", "tel:", "javascript:", "sms:")):
        return None
    target = urljoin(base, href.strip()).split("#", 1)[0]
    return target if urlsplit(target).scheme in ("http", "https") else None


def _clickable_target(element: Tag, base: str) -> str | None:
    """Where a call to action leads: its own `href`, or that of the link it sits in.

    A button or a submit input with no enclosing link has no page to follow — a form's
    `action` is where it posts, not a page to read — so it has no target.
    """
    link = element if element.name == "a" and element.get("href") else None
    if link is None:
        parent = element.find_parent("a", href=True)
        link = parent if isinstance(parent, Tag) else None
    if link is None:
        return None
    return _link_target(base, str(link["href"]))


def booking_target_flow(outcome: FetchOutcome) -> str | None:
    """What on a fetched booking-link target shows a real booking flow, or `None`.

    A page served by a booking provider; a known booking vendor's host in an iframe
    `src`, a script `src` or a link `href` (Urban Betty's scheduler is a Phorest iframe on a
    page with no date input of its own); a known widget signature; or a date or time
    input. A Google host is never a signal (`NEVER_BOOKING_HOSTS`). The answer is a short
    description for the evidence, never a guess.
    """
    final = outcome.final_url or outcome.url
    provider = booking_host(final)
    if provider is not None:
        return f"the page is served by {provider}"
    if outcome.text is None:
        return None
    soup = BeautifulSoup(outcome.text, "lxml")
    for tag, attribute in (("iframe", "src"), ("script", "src"), ("a", "href"), ("link", "href")):
        for element in find_tags(soup, tag, **{attribute: True}):
            provider = booking_host(urljoin(final, str(element[attribute]).strip()))
            if provider is not None:
                return f"the page embeds or links to {provider}: {clip(str(element), 160)}"
    hits = find_signatures(outcome.text.lower(), BOOKING_SIGNATURES)
    if hits:
        return f"the page carries a {hits[0].label} widget"
    for field in find_tags(soup, ["input", "select"]):
        kind = str(field.get("type", "")).lower()
        if kind == "hidden":
            continue
        if kind in DATE_TIME_INPUT_TYPES:
            return f"the page has a {kind} input: {clip(str(field), 160)}"
        classes = field.get("class") or []
        words = " ".join(
            [str(field.get(attr, "")) for attr in ("name", "id", "placeholder", "aria-label")]
            + [str(c) for c in (classes if isinstance(classes, list) else [classes])]
        ).lower()
        if DATE_TIME_WORD.search(words):
            return f"the page has a date or time field: {clip(str(field), 160)}"
    return None


def booking_onward_hop(outcome: FetchOutcome) -> str | None:
    """Where a target with no scheduler of its own leads on to booking, or `None` (v0.12.1).

    Only two things count. (a) A link whose text or href carries a booking word (book,
    booking, appointment, schedule, reserve). (b) A choice of locations: two or more
    links, or a select with two or more options, that read like a branch, store, location
    or city picker. A bare outside link is **not** onward booking. ATX's contact page has
    "Website Crafted by Enlightened Owl Digital" in its footer, and counting that would
    silence the very finding this check exists for. Designer credits, trade bodies,
    badges, directories, social and Google are ignored wherever they sit. Links back to
    this page never count. Nothing is followed; the hop is only named.
    """
    if outcome.text is None:
        return None
    page = outcome.final_url or outcome.url
    soup = BeautifulSoup(outcome.text, "lxml")
    locations: list[str] = []
    for link in find_tags(soup, "a", href=True):
        target = _link_target(page, str(link["href"]))
        if target is None or same_page(target, page):
            continue
        host = urlsplit(target).hostname or ""
        if is_never_booking_host(host) or is_social_host(host):
            continue
        words = _words(f"{link.get_text(' ', strip=True)} {_clickable_label(link)} {target}")
        if words & ONWARD_BOOKING_WORDS:
            return f"'{_clickable_label(link) or target}' leads on to {target}"
        if words & LOCATION_WORDS and target not in locations:
            locations.append(target)
    if len(locations) >= 2:
        return f"the page offers a choice of locations, e.g. {locations[0]}"
    for select in find_tags(soup, "select"):
        named = _words(" ".join(str(select.get(a, "")) for a in ("name", "id", "aria-label")))
        if named & LOCATION_WORDS and len(find_tags(select, "option")) >= 2:
            return f"the page asks which location: {clip(str(select), 160)}"
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def looks_script_built(outcome: FetchOutcome) -> bool:
    """A page whose content JavaScript assembles: what it offers cannot be read here."""
    if outcome.text is None:
        return False
    soup = BeautifulSoup(outcome.text, "lxml")
    cleaned = _cleaned(BeautifulSoup(outcome.text, "lxml"))
    text = _text_of(cleaned.body or cleaned)
    return bool(_js_shell(soup, outcome.final_url or outcome.url, text).value)


def _clickables(soup: BeautifulSoup) -> list[Tag]:
    """What a visitor can click, in document order."""
    return [
        tag
        for tag in find_tags(soup, True)
        if tag.name in ("a", "button")
        or (tag.name == "input" and str(tag.get("type", "")).lower() in ("submit", "button"))
        or str(tag.get("role", "")).lower() == "button"
    ]


def _clickable_label(tag: Tag) -> str:
    text = tag.get_text(" ", strip=True)
    if text:
        return text
    for attribute in ("aria-label", "title", "value"):
        value = _attr(tag, attribute)
        if value:
            return value
    return ""


def _is_booking_path(href: str) -> bool:
    lowered = href.lower()
    if lowered.startswith(("mailto:", "tel:", "javascript:", "#")):
        return False
    segments = {segment for segment in urlsplit(lowered).path.split("/") if segment}
    return bool(segments & BOOKING_HREF_SEGMENTS)


def _live_chat(lowered: str, url: str) -> CheckResult:
    """A live-chat or messaging widget, recognised by its script host."""
    hits = find_signatures(lowered, CHAT_SIGNATURES)
    if hits:
        return CheckResult(hits[0].label, evidence_text=hits[0].evidence, evidence_url=url)
    return CheckResult(
        False,
        evidence_text="No known live chat or messaging widget script on the homepage",
        evidence_url=url,
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


def _structured_data(soup: BeautifulSoup, url: str) -> Checks:
    """What structured data the page carries, in any of the three syntaxes (v0.12.0, item 1).

    * `structured_data` — the normalised type of the first LocalBusiness-family item
      (`Plumber`, never the full schema.org URL), or `False`;
    * `structured_data_types` — every type seen, so "schema, but not the local kind" can be
      told from "no schema at all";
    * `structured_data_errors` — how many JSON-LD blocks do not parse, the first quoted;
    * `local_business` — the fields of the first LocalBusiness item, or `False`.
    """
    found = structured_data.read(soup)
    first = found.local_businesses[0] if found.local_businesses else None
    broken = found.broken_blocks[0] if found.broken_blocks else None
    return {
        "structured_data": CheckResult(
            first.type if first is not None else False,
            evidence_text=(
                first.evidence
                if first is not None
                else "No LocalBusiness structured data (JSON-LD, microdata or RDFa) on the homepage"
            ),
            evidence_url=url,
        ),
        "structured_data_types": CheckResult(
            found.types,
            evidence_text=(
                ", ".join(found.types) if found.types else "No structured data on the homepage"
            ),
            evidence_url=url,
        ),
        "structured_data_errors": CheckResult(
            {
                "count": len(found.broken_blocks),
                "type_hint": broken.type_hint if broken is not None else None,
                "error": broken.error if broken is not None else None,
            },
            evidence_text=(
                _broken_evidence(broken)
                if broken is not None
                else "Every JSON-LD block on the homepage parses"
            ),
            evidence_url=url,
            evidence_max=BROKEN_BLOCK_EVIDENCE_CHARS,
        ),
        "local_business": CheckResult(
            (
                {
                    "type": first.type,
                    "syntax": first.syntax,
                    "items": len(found.local_businesses),
                    **first.fields,
                }
                if first is not None
                else False
            ),
            evidence_text=first.evidence if first is not None else None,
            evidence_url=url,
        ),
    }


# Enough of a broken JSON-LD block for a person to find the fault themselves: the parser's
# message, the text around the point it gave up, then the block itself (v0.12.0, run 2).
BROKEN_BLOCK_EVIDENCE_CHARS = 2000
BROKEN_BLOCK_CONTEXT_CHARS = 160


def _broken_evidence(broken: structured_data.BrokenBlock) -> str:
    start = max(broken.position - BROKEN_BLOCK_CONTEXT_CHARS, 0)
    end = broken.position + BROKEN_BLOCK_CONTEXT_CHARS
    around = broken.text[start:end]
    marker = broken.position - start
    near = f"{around[:marker]} <<HERE>> {around[marker:]}"
    return (
        f"JSON-LD does not parse: {broken.error}. Near the error: {near} "
        f"|| Whole block: {broken.raw}"
    )


def _tech_stack(soup: BeautifulSoup, lowered: str, url: str) -> CheckResult:
    """The platforms the page gives away, and — separately — a website builder, if any.

    `platforms` is context and matches loosely. `builder` is a claim made to the business
    ("built with Wix"), so it needs a generator tag naming the builder, a script the builder
    serves, or a class its templates write (`BUILDER_SIGNATURES`) — never a link to the
    builder, and never an image or other asset that merely sits on the builder's CDN.
    """
    generator_tag = _meta_tag(soup, "generator")
    generator = _attr(generator_tag, "content")
    platforms: list[str] = []
    evidence: list[str] = []
    builder: dict[str, str] | None = None

    if generator:
        label = generator_label(generator)
        if label is not None:
            platforms.append(label)
            if label in BUILDER_LABELS:
                builder = {"label": label, "evidence": str(generator_tag)}
        evidence.append(str(generator_tag))

    for hit in find_signatures(lowered, TECH_SIGNATURES):
        if hit.label not in platforms:
            platforms.append(hit.label)
            evidence.append(hit.pattern)

    if builder is None:
        builder = _builder_from_markup(soup)
        if builder is not None and builder["label"] not in platforms:
            platforms.append(builder["label"])
            evidence.append(builder["evidence"])

    return CheckResult(
        {"generator": generator or None, "platforms": platforms, "builder": builder},
        evidence_text="; ".join(evidence) if evidence else "No platform signature on the homepage",
        evidence_url=url,
    )


def _builder_from_markup(soup: BeautifulSoup) -> dict[str, str] | None:
    """A builder named by a script it serves or a class its templates write, or `None`."""
    for script in find_tags(soup, "script", src=True):
        source = str(script["src"]).strip().lower()
        location = source.split("//", 1)[1] if "//" in source else source
        for signature in BUILDER_SIGNATURES:
            if any(location.startswith(host) for host in signature.script_hosts):
                return {"label": signature.label, "evidence": _opening(script)}
    for tag in find_tags(soup, class_=True):
        classes = [str(name).lower() for name in (tag.get("class") or [])]
        for signature in BUILDER_SIGNATURES:
            if any(
                name.startswith(prefix) for name in classes for prefix in signature.class_prefixes
            ):
                return {"label": signature.label, "evidence": _opening(tag)}
    return None


def _opening(tag: Tag) -> str:
    return str(tag).split(">", 1)[0][: QUOTED_TAG_CHARS * 2] + ">"


def _copyright_year(text: str, url: str, now: datetime) -> CheckResult:
    """The highest year printed next to a copyright mark, and the line it is on.

    Read from the page's **visible text**, not its HTML. A copyright notice is something a
    visitor reads, so cutting a window out of the markup produced evidence that began and
    ended mid-tag — true, and unreadable. Taken from the text it reads
    "© 2016 Barton Creek Plumbing LLC. All rights reserved.", which is what is actually
    printed at the foot of the page.

    Since v0.12.0 a **future** year is kept too, because it is a finding of its own (a
    template placeholder, "© 2035"), and a range's later year is always the one that
    counts: "© 2018-2035" is a future year, never a stale 2018. A future year is only
    believed when it sits directly after the mark (`COPYRIGHT_ADJACENT_PATTERN`).

    After production run 4: a year is exactly four digits ("© 20015" is none), and a range
    with no end year in the source ("© 2006 - | …", the end written by a script) makes the
    year `"unknown"` — no claim either way. The evidence says the year is as written in
    the HTML source.
    """
    for match in COPYRIGHT_PATTERN.finditer(text):
        if OPEN_RANGE_TAIL.match(text, match.end()):
            return CheckResult(
                "unknown",
                evidence_text=(
                    "The copyright range has no end year in the page's HTML source; a "
                    "script probably fills it in, so the year is unknown: "
                    + snippet_forward(text, match.start(), COPYRIGHT_EVIDENCE_CHARS)
                ),
                evidence_url=url,
            )
    years: list[tuple[int, str]] = []
    for pattern in (COPYRIGHT_RANGE_PATTERN, COPYRIGHT_PATTERN):
        for match in pattern.finditer(text):
            year = int(match.group(1))
            if EARLIEST_COPYRIGHT_YEAR <= year <= now.year:
                years.append((year, snippet_forward(text, match.start(), COPYRIGHT_EVIDENCE_CHARS)))
    for match in COPYRIGHT_ADJACENT_PATTERN.finditer(text):
        for group in (1, 2):
            raw = match.group(group)
            if raw is None:
                continue
            year = int(raw)
            if now.year < year <= now.year + FUTURE_COPYRIGHT_MAX_YEARS:
                years.append((year, snippet_forward(text, match.start(), COPYRIGHT_EVIDENCE_CHARS)))
    if not years:
        return CheckResult(
            False, evidence_text="No copyright year on the homepage", evidence_url=url
        )
    best = max(years, key=lambda item: item[0])
    return CheckResult(best[0], evidence_text=AS_IN_SOURCE + best[1], evidence_url=url)


def _placeholder_text(cleaned: BeautifulSoup, text: str, url: str) -> CheckResult:
    """Template text nobody replaced: "Your Company", "Business Name", "Lorem ipsum".

    The footer phrases are only looked for in short pieces of footer text — inside a
    `<footer>`, a `role="contentinfo"` element, or an element whose id or class says
    footer — and on the line a copyright notice is printed on, because "Company Name" is
    also an ordinary label in a quote form and "your company" an ordinary phrase in a
    testimonial. "Lorem ipsum" is a placeholder wherever it appears. Whether a phrase is
    in fact part of the business's own name is decided by the finding, which knows it.
    """
    regions: list[str] = []
    for footer in _footer_regions(cleaned):
        # A footer's own quote or newsletter form labels its fields "Company Name"; that is
        # a field label, not placeholder text. `cleaned` is a throwaway copy, and this is
        # the last thing read from it.
        for control in find_tags(footer, ["form", "label", "button", "select", "textarea"]):
            control.decompose()
        # Only short pieces of footer text: a template placeholder stands alone ("Your
        # Company", "Business Name, LLC"). Running text is prose, and in production a
        # customer testimonial in a footer ("…impressed by … your company") was read as one.
        for piece in footer.find_all(string=True):
            words = " ".join(str(piece).split())
            if words and len(words) <= PLACEHOLDER_TEXT_MAX_CHARS:
                regions.append(words)
    for match in COPYRIGHT_PATTERN.finditer(text):
        regions.append(snippet_forward(text, match.start(), COPYRIGHT_LINE_CHARS))

    found: list[str] = []
    evidence: str | None = None
    for region, phrases in (
        *((region, FOOTER_PLACEHOLDER_PHRASES) for region in regions),
        (text, PAGE_PLACEHOLDER_PHRASES),
    ):
        lowered = region.lower()
        for phrase in phrases:
            for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered):
                printed = region[match.start() : match.end()]
                # A longer phrase ("your company name") wins over the one inside it.
                if any(phrase in other.lower() for other in found):
                    break
                found.append(printed)
                if evidence is None:
                    evidence = trim_to_words(
                        region, max(match.start() - 60, 0), min(match.end() + 60, len(region))
                    )
                break
    return CheckResult(
        found[:5] or False,
        evidence_text=evidence or "No template placeholder text on the homepage",
        evidence_url=url,
    )


def _footer_regions(cleaned: BeautifulSoup) -> list[Tag]:
    """The page's footer elements, outermost first, never one inside another."""
    regions: list[Tag] = []
    for tag in find_tags(cleaned, True):
        if tag.name in ("html", "body"):
            continue
        marker = " ".join([str(_attr(tag, "id") or ""), str(_attr(tag, "class") or "")]).lower()
        if (
            (
                tag.name == "footer"
                or str(tag.get("role", "")).lower() == "contentinfo"
                or "footer" in marker
            )
            and not any(region in tag.parents for region in regions)
            # A wrapper classed `has-footer` around the whole page is not a footer.
            and tag.find(["h1", "main"]) is None
        ):
            regions.append(tag)
    return regions


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


# --- page quality: counts, never judgements ---------------------------------------------


def _images_without_alt(soup: BeautifulSoup, url: str) -> CheckResult:
    """How many images carry no `alt` attribute at all, of how many a reader is shown.

    `alt=""` is not counted: an empty alternative is the correct markup for a decorative
    image. Nor is an image hidden from assistive technology (`aria-hidden="true"`,
    `role="presentation"` or `role="none"`), which is exactly what Lighthouse ignores too.
    """
    shown = [image for image in find_tags(soup, "img") if not _hidden_from_readers(image)]
    missing = [image for image in shown if image.get("alt") is None]
    return CheckResult(
        {"images": len(shown), "without_alt": len(missing)},
        evidence_text=(
            _first_tags(missing) if missing else f"All {len(shown)} images carry an alt attribute"
        ),
        evidence_url=url,
    )


def _unlabelled_inputs(soup: BeautifulSoup, url: str) -> CheckResult:
    """Form fields a screen reader cannot name: no `<label>`, `aria-label` or `title`.

    A placeholder is not a label — it disappears as soon as someone types. Hidden fields
    and buttons are not fields a person fills in, so they are not counted at all.
    """
    labelled_ids = {
        str(label["for"]).strip() for label in find_tags(soup, "label", attrs={"for": True})
    }
    fields = [
        field for field in find_tags(soup, ["input", "select", "textarea"]) if _fillable(field)
    ]
    unlabelled = [field for field in fields if not _has_label(field, labelled_ids)]
    return CheckResult(
        {"fields": len(fields), "unlabelled": len(unlabelled)},
        evidence_text=(
            _first_tags(unlabelled)
            if unlabelled
            else f"All {len(fields)} form fields have an associated label"
        ),
        evidence_url=url,
    )


def _word_count(text: str, url: str) -> CheckResult:
    """Words of visible text. The count is the evidence; the text itself is not quoted.

    Quoting the page here would copy whatever its header prints into a check that never
    expires — on a real homepage that was a named person's email address. The visible text
    is already kept, with its own expiry, as `page_text`.
    """
    words = len(text.split())
    return CheckResult(
        words, evidence_text=f"The homepage carries {words} words of visible text", evidence_url=url
    )


def _heading_structure(soup: BeautifulSoup, url: str) -> CheckResult:
    """The page's headings in order, and where a level is skipped on the way down.

    `levels` lists every heading with text, in document order. A skip is a heading more
    than one level deeper than the one before it (`h2` → `h4`); going back up is never a
    skip. `sections_below_h1` is `None` when there is no `h1`, which is its own finding.
    """
    headings = [
        tag
        for tag in find_tags(soup, ["h1", "h2", "h3", "h4", "h5", "h6"])
        if tag.get_text(strip=True)
    ]
    levels = [int(tag.name[1]) for tag in headings]
    skips: list[str] = []
    for previous, current in pairwise(headings):
        if int(current.name[1]) > int(previous.name[1]) + 1:
            skips.append(f"{previous.name} to {current.name}: {clip(str(current))}")
    first_h1 = next((index for index, level in enumerate(levels) if level == 1), None)
    sections_below_h1 = (
        None if first_h1 is None else sum(1 for level in levels[first_h1 + 1 :] if level > 1)
    )
    if skips:
        evidence = "; ".join(skips)
    elif headings:
        evidence = " ".join(f"<{tag.name}>" for tag in headings)
    else:
        evidence = "No headings with text on the homepage"
    return CheckResult(
        {
            "levels": [f"h{level}" for level in levels],
            "sections_below_h1": sections_below_h1,
            "skips": [skip.split(":", 1)[0] for skip in skips],
            "first_h1": clip(str(headings[first_h1])) if first_h1 is not None else None,
        },
        evidence_text=evidence,
        evidence_url=url,
    )


def _hidden_from_readers(tag: Tag) -> bool:
    return str(tag.get("aria-hidden", "")).lower() == "true" or str(
        tag.get("role", "")
    ).lower() in ("presentation", "none")


NOT_FILLABLE_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})


def _fillable(field: Tag) -> bool:
    if field.name != "input":
        return True
    return str(field.get("type", "text")).strip().lower() not in NOT_FILLABLE_INPUT_TYPES


def _has_label(field: Tag, labelled_ids: set[str]) -> bool:
    if field.find_parent("label") is not None:
        return True
    identifier = _attr(field, "id")
    if identifier and identifier in labelled_ids:
        return True
    return any(_attr(field, attribute) for attribute in ("aria-label", "aria-labelledby", "title"))


# How many offending tags are quoted, and how much of each, so three always fit.
QUOTED_TAGS = 3
QUOTED_TAG_CHARS = 95


def _first_tags(tags: list[Tag]) -> str:
    """The first few offending tags, verbatim and each cut short: evidence, not a dump."""
    return " ".join(" ".join(str(tag).split())[:QUOTED_TAG_CHARS] for tag in tags[:QUOTED_TAGS])


# --- small helpers ---------------------------------------------------------------------


def visible_text(soup: BeautifulSoup) -> str:
    """The text a person would read: no scripts, styles, templates or comments."""
    cleaned = _cleaned(soup)
    return _text_of(cleaned.body or cleaned)


def _cleaned(soup: BeautifulSoup) -> BeautifulSoup:
    """A copy of the page with everything a reader never sees removed."""
    clone = BeautifulSoup(str(soup), "lxml")
    for tag in find_tags(clone, ["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return clone


def _text_of(node: BeautifulSoup | Tag) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


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


def _first_href(soup: BeautifulSoup, prefix: str) -> Tag | None:
    tags = _hrefs(soup, prefix)
    return tags[0] if tags else None


def _hrefs(soup: BeautifulSoup, prefix: str) -> list[Tag]:
    return [
        tag
        for tag in find_tags(soup, "a", href=True)
        if str(tag["href"]).strip().lower().startswith(prefix)
    ]


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
