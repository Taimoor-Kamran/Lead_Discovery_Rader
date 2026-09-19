"""What a page looks like when it uses a particular platform, widget or profile.

Everything here is **data**. Adding a booking tool or a shop platform is a line in a
table, not a new code path, which is the only way a signature list stays maintainable —
and it means one test can walk every signature and prove each one is actually matched.

A pattern is a lowercase substring looked for in the raw HTML. Substrings rather than
regexes on purpose: they cannot backtrack, they are readable by whoever adds the next one,
and the evidence we store is verbatim: the whole tag the hit sits in (see `snippet_around`),
never a window cut blind through the middle of one.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# How much of the page around a hit is kept as evidence.
EVIDENCE_WINDOW = 120
# The most markup one hit may cite, for the rare signature that sits inside a huge tag.
EVIDENCE_MAX_SNIPPET = 300


@dataclass(frozen=True)
class Signature:
    """One recognisable thing, and the strings that give it away."""

    key: str
    label: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class SignatureHit:
    """A matched signature and the verbatim text it was matched in."""

    key: str
    label: str
    pattern: str
    evidence: str


# --- booking and scheduling ----------------------------------------------------------

BOOKING_SIGNATURES: tuple[Signature, ...] = (
    Signature("calendly", "Calendly", ("calendly.com", "calendly-badge", "calendly-inline")),
    Signature("acuity", "Acuity Scheduling", ("acuityscheduling.com", "app.squarespacescheduling")),
    Signature("square_appointments", "Square Appointments", ("squareup.com/appointments",)),
    Signature("setmore", "Setmore", ("setmore.com", "my.setmore.com")),
    Signature("housecall_pro", "Housecall Pro", ("housecallpro.com", "book.housecallpro")),
    Signature("jobber", "Jobber", ("getjobber.com", "clienthub.getjobber.com")),
    Signature("servicetitan", "ServiceTitan", ("servicetitan.com", "book.servicetitan")),
    Signature("booksy", "Booksy", ("booksy.com",)),
    Signature("vagaro", "Vagaro", ("vagaro.com",)),
    Signature("opentable", "OpenTable", ("opentable.com", "opentable.co.uk")),
    Signature("mindbody", "Mindbody", ("mindbodyonline.com", "clients.mindbodyonline")),
    Signature("schedulicity", "Schedulicity", ("schedulicity.com",)),
    Signature("simplybook", "SimplyBook.me", ("simplybook.me",)),
    Signature("appointlet", "Appointlet", ("appointlet.com",)),
    Signature("youcanbookme", "YouCanBook.me", ("youcanbook.me",)),
)

# Link text (or a button label) that offers to book without naming a tool. Kept narrow on
# purpose: "contact us" is not booking, and guessing would invent a fact.
BOOKING_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bbook\s+(now|online|an?\s+appointment|a\s+service)\b"),
    re.compile(r"\bschedule\s+(online|service|an?\s+appointment|a\s+visit)\b"),
    re.compile(r"\brequest\s+(a\s+)?quote\b"),
    re.compile(r"\bbook\s+a\s+(consultation|call)\b"),
)

# --- e-commerce ----------------------------------------------------------------------

ECOMMERCE_SIGNATURES: tuple[Signature, ...] = (
    Signature("shopify", "Shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com")),
    Signature(
        "woocommerce",
        "WooCommerce",
        ("woocommerce", "wc-add-to-cart", "wp-content/plugins/woocommerce"),
    ),
    Signature("bigcommerce", "BigCommerce", ("bigcommerce.com", "cdn11.bigcommerce.com")),
    Signature(
        "squarespace_commerce", "Squarespace Commerce", ("squarespace-commerce", "sqs-add-to-cart")
    ),
    Signature("magento", "Magento", ("mage/cookies", "magento_theme")),
    Signature("ecwid", "Ecwid", ("ecwid.com", "ecwid_store")),
    Signature("square_online", "Square Online", ("square.site/cart", "squareup.com/store")),
)

# A cart or checkout link is e-commerce whoever built it.
CART_LINK_PATTERNS: tuple[str, ...] = ("/cart", "/checkout", "add-to-cart", "/basket", "/shop/cart")

# --- tech stack ----------------------------------------------------------------------

TECH_SIGNATURES: tuple[Signature, ...] = (
    Signature("wordpress", "WordPress", ("wp-content", "wp-includes", "wp-json")),
    Signature("wix", "Wix", ("wix.com", "wixstatic.com", "wixsite.com", "_wixcssimports")),
    Signature(
        "squarespace", "Squarespace", ("squarespace.com", "static1.squarespace.com", "sqs-block")
    ),
    Signature("shopify_platform", "Shopify", ("cdn.shopify.com", "myshopify.com")),
    Signature("webflow", "Webflow", ("webflow.com", "webflow.js", "w-mod-js")),
    Signature("godaddy_builder", "GoDaddy Website Builder", ("godaddysites.com", "img1.wsimg.com")),
    Signature("weebly", "Weebly", ("weebly.com", "weeblysite.com", "weebly-footer")),
    Signature("duda", "Duda", ("dudamobile.com", "dudaone", "multiscreensite.com")),
    Signature("joomla", "Joomla", ("/media/jui/", "joomla!", "com_content")),
    Signature("drupal", "Drupal", ("drupal.js", "/sites/default/files", "drupal-settings-json")),
    Signature("nextjs", "Next.js", ("__next_data__", "/_next/static")),
    Signature("react", "React", ("data-reactroot", "react-dom", "__react")),
    Signature("bootstrap", "Bootstrap", ("bootstrap.min.css", "bootstrap.bundle")),
    Signature("elementor", "Elementor", ("elementor-page", "/elementor/assets")),
    Signature("gtm", "Google Tag Manager", ("googletagmanager.com",)),
)

# `<meta name="generator">` values, which say it outright.
GENERATOR_LABELS: tuple[tuple[str, str], ...] = (
    ("wordpress", "WordPress"),
    ("wix", "Wix"),
    ("squarespace", "Squarespace"),
    ("shopify", "Shopify"),
    ("webflow", "Webflow"),
    ("weebly", "Weebly"),
    ("duda", "Duda"),
    ("joomla", "Joomla"),
    ("drupal", "Drupal"),
    ("hugo", "Hugo"),
    ("jekyll", "Jekyll"),
    ("godaddy", "GoDaddy Website Builder"),
)

# --- social profiles ------------------------------------------------------------------

# Platform → the hosts that mean "this business links to its page there". Recording the
# link is all we do: no profile is ever fetched, which is the blueprint's hard rule.
SOCIAL_PLATFORMS: tuple[Signature, ...] = (
    Signature("facebook", "Facebook", ("facebook.com", "fb.com", "fb.me")),
    Signature("instagram", "Instagram", ("instagram.com",)),
    Signature("x", "X", ("twitter.com", "x.com")),
    Signature("linkedin", "LinkedIn", ("linkedin.com",)),
    Signature("youtube", "YouTube", ("youtube.com", "youtu.be")),
    Signature("tiktok", "TikTok", ("tiktok.com",)),
    Signature("yelp", "Yelp", ("yelp.com",)),
    Signature("nextdoor", "Nextdoor", ("nextdoor.com",)),
    Signature("google", "Google", ("google.com/maps", "g.page", "goo.gl/maps", "maps.app.goo.gl")),
)

# --- JSON-LD ---------------------------------------------------------------------------

# schema.org LocalBusiness plus the subtypes a local-services dataset actually meets.
LOCAL_BUSINESS_TYPES = frozenset(
    {
        "localbusiness",
        "homeandconstructionbusiness",
        "plumber",
        "electrician",
        "hvacbusiness",
        "roofingcontractor",
        "generalcontractor",
        "housepainter",
        "locksmith",
        "movingcompany",
        "professionalservice",
        "legalservice",
        "attorney",
        "accountingservice",
        "insuranceagency",
        "realestateagent",
        "automotivebusiness",
        "autorepair",
        "autobodyshop",
        "medicalbusiness",
        "dentist",
        "physician",
        "veterinarycare",
        "healthandbeautybusiness",
        "beautysalon",
        "hairsalon",
        "dayspa",
        "healthclub",
        "sportsactivitylocation",
        "childcare",
        "foodestablishment",
        "restaurant",
        "cafeorcoffeeshop",
        "bakery",
        "bar",
        "store",
        "selfstorage",
        "emergencyservice",
        "cleaningservice",
        "pestcontrolservice",
        "landscaping",
    }
)


def find_signatures(haystack: str, signatures: Iterable[Signature]) -> list[SignatureHit]:
    """Every signature present in `haystack` (which must already be lowercase)."""
    hits: list[SignatureHit] = []
    for signature in signatures:
        for pattern in signature.patterns:
            position = haystack.find(pattern)
            if position < 0:
                continue
            hits.append(
                SignatureHit(
                    key=signature.key,
                    label=signature.label,
                    pattern=pattern,
                    evidence=snippet_around(haystack, position, len(pattern)),
                )
            )
            break
    return hits


def snippet_around(haystack: str, position: int, length: int, window: int = EVIDENCE_WINDOW) -> str:
    """The verbatim markup around a hit, never cut through the middle of a tag.

    A signature is matched in the HTML rather than in what a reader sees, so this evidence
    is markup by nature — `cdn.shopify.com` appears in a `src`, never in a sentence. What
    it must not be is a *fragment*: a window cut blind lands mid-tag and reads as garbage
    (`r" content="wordpress 6.5.2">`). So when the hit sits inside a tag, which is where
    almost every signature lives, the whole tag is the evidence; when it sits in the page's
    text the window is trimmed back to whole words. Either way nothing is invented, and a
    person can recognise what they are being shown.
    """
    enclosing = enclosing_tag(haystack, position, position + length)
    if enclosing is not None:
        start, end = enclosing
        return " ".join(haystack[start:end].split())[:EVIDENCE_MAX_SNIPPET]
    start = max(position - window // 2, 0)
    end = min(position + length + window // 2, len(haystack))
    return trim_to_words(haystack, start, end)


def enclosing_tag(haystack: str, start: int, end: int) -> tuple[int, int] | None:
    """The bounds of the one tag that contains `haystack[start:end]`, or `None` for text."""
    opened = haystack.rfind("<", 0, start + 1)
    if opened < 0:
        return None
    closed = haystack.find(">", opened)
    # A `>` before the hit ends means the hit is not inside this tag but after it.
    if closed < 0 or closed < end - 1:
        return None
    return opened, closed + 1


def trim_to_words(text: str, start: int, end: int, *, trim_start: bool = True) -> str:
    """`text[start:end]`, pulled in to whole-word boundaries, whitespace collapsed.

    A snippet is read by a person, so it may not begin or end halfway through a word.
    `trim_start=False` is for a cut that starts somewhere deliberate — a copyright mark,
    say — where the first characters are the point of the snippet.
    """
    words = text[start:end].split()
    if words and trim_start and start > 0 and not text[start - 1].isspace():
        words = words[1:]
    if words and end < len(text) and not text[end].isspace():
        words = words[:-1]
    return " ".join(words)


def snippet_forward(text: str, position: int, limit: int) -> str:
    """The text from `position` on, cut at `limit` and trimmed to a whole word.

    Used where the interesting thing is the start of the snippet and what follows it, such
    as the line a copyright notice is printed on.
    """
    return trim_to_words(text, position, min(position + limit, len(text)), trim_start=False)


def booking_text_match(text: str) -> str | None:
    """The first booking-style call to action in `text` (lowercase), or `None`."""
    for pattern in BOOKING_TEXT_PATTERNS:
        found = pattern.search(text)
        if found is not None:
            return found.group(0)
    return None


def generator_label(generator: str) -> str | None:
    """Map a `<meta name="generator">` value onto a platform name we recognise."""
    lowered = generator.lower()
    for needle, label in GENERATOR_LABELS:
        if needle in lowered:
            return label
    return None


def is_local_business_type(raw: object) -> bool:
    """Whether a JSON-LD `@type` (a string or a list) is LocalBusiness or a subtype."""
    values: Sequence[object] = raw if isinstance(raw, list) else [raw]
    for value in values:
        if isinstance(value, str) and value.strip().lower().split("/")[-1] in LOCAL_BUSINESS_TYPES:
            return True
    return False


ALL_SIGNATURE_GROUPS: tuple[tuple[str, tuple[Signature, ...]], ...] = (
    ("booking", BOOKING_SIGNATURES),
    ("ecommerce", ECOMMERCE_SIGNATURES),
    ("tech", TECH_SIGNATURES),
    ("social", SOCIAL_PLATFORMS),
)
