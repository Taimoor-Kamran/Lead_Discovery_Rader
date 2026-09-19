"""What a page looks like when it uses a particular platform, widget or profile.

Everything here is **data**. Adding a booking tool or a shop platform is a line in a
table, not a new code path, which is the only way a signature list stays maintainable —
and it means one test can walk every signature and prove each one is actually matched.

A pattern is a lowercase substring looked for in the raw HTML. Substrings rather than
regexes on purpose: they cannot backtrack, they are readable by whoever adds the next
one, and the evidence we store is simply the text around the hit.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# How much of the page around a hit is kept as evidence.
EVIDENCE_WINDOW = 120


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
    """The verbatim text around a hit, for the evidence field. Never invented."""
    start = max(position - window // 2, 0)
    end = min(position + length + window // 2, len(haystack))
    return " ".join(haystack[start:end].split())


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
