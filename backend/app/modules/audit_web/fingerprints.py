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
    re.compile(r"\bbook\s+(now|online|an?\s+appointment|a\s+service|service)\b"),
    re.compile(r"\bschedule\s+(online|now|a\s+service|service|an?\s+appointment|a\s+visit)\b"),
    re.compile(r"\brequest\s+(a\s+)?(quote|service|an?\s+appointment)\b"),
    re.compile(r"\bbook\s+a\s+(consultation|call)\b"),
)

# A link whose *path* is a booking page, whatever its text says (an icon, an image, a
# label in another language). Matched against whole path segments, never substrings, so
# `/books` or `/bookkeeping` is not booking.
BOOKING_HREF_SEGMENTS = frozenset(
    {
        "book",
        "booking",
        "bookings",
        "book-now",
        "book-online",
        "book-a-service",
        "book-service",
        "book-appointment",
        "book-an-appointment",
        "schedule",
        "schedule-service",
        "schedule-online",
        "schedule-appointment",
        "schedule-an-appointment",
        "appointment",
        "appointments",
        "request-service",
    }
)

# --- live chat and messaging ---------------------------------------------------------

# Script hosts and globals of the chat widgets the spec names. Hosts, not brand names: the
# bare word "crisp" is a WordPress CSS token (`--wp--preset--shadow--crisp`) on every
# block-theme site, and "drift" or "intercom" are ordinary words a page can print.
CHAT_SIGNATURES: tuple[Signature, ...] = (
    Signature("intercom", "Intercom", ("widget.intercom.io", "js.intercomcdn.com")),
    Signature("drift", "Drift", ("js.driftt.com", "js.drift.com")),
    Signature("tawk", "Tawk.to", ("embed.tawk.to",)),
    Signature("crisp", "Crisp", ("client.crisp.chat",)),
    Signature(
        "hubspot_chat",
        "HubSpot chat",
        ("js.usemessages.com", "js-na1.usemessages.com", "hubspot-messages-iframe"),
    ),
    Signature("zendesk", "Zendesk", ("static.zdassets.com", "ze-snippet", "zopim.com")),
    Signature("tidio", "Tidio", ("code.tidio.co",)),
    Signature("livechat", "LiveChat", ("cdn.livechatinc.com",)),
    Signature("facebook_chat", "Facebook Customer Chat", ("fb-customerchat", "xfbml.customerchat")),
)

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


# The website builders whose use is itself a finding (v0.12.0, item 6), and the evidence
# strong enough to say so to the business: a script the builder serves, or a class name its
# templates put on the page. A generator tag naming the builder counts too (read separately).
# An *asset* on the builder's CDN never counts: in production a Squarespace-hosted og:image
# was the only "evidence" for one site, and an image URL proves where an image is stored,
# not what built the page. Hosts are matched against a script's `src` (host and path);
# classes by prefix, case-insensitive.
@dataclass(frozen=True)
class BuilderSignature:
    key: str
    label: str
    script_hosts: tuple[str, ...]
    class_prefixes: tuple[str, ...] = ()


BUILDER_SIGNATURES: tuple[BuilderSignature, ...] = (
    BuilderSignature("wix", "Wix", ("static.parastorage.com",), ("wixui-",)),
    BuilderSignature(
        "squarespace",
        "Squarespace",
        ("assets.squarespace.com", "static1.squarespace.com/static/vta"),
        ("sqs-block", "sqs-layout"),
    ),
    BuilderSignature(
        "godaddy_builder",
        "GoDaddy Website Builder",
        ("img1.wsimg.com/blobby/go", "img1.wsimg.com/ceph-p3-01/website-builder"),
    ),
    BuilderSignature(
        "duda", "Duda", ("static.cdn-website.com", "dd-cdn.multiscreensite.com"), ("dmbody",)
    ),
    BuilderSignature("weebly", "Weebly", ("editmysite.com",), ("wsite-",)),
)
BUILDER_LABELS = frozenset(signature.label for signature in BUILDER_SIGNATURES)

# --- bot protection --------------------------------------------------------------------

# A site that answered with one of these instead of its homepage (v0.12.0, item 3b). Only a
# challenge *status* counts (403, 429, 503): Cloudflare also injects
# `/cdn-cgi/challenge-platform/` scripts into ordinary 200 pages, which are real homepages.
BOT_CHALLENGE_STATUSES = frozenset({403, 429, 503})
# (vendor, lowercase marker found in the body or the exact lowercase title)
BOT_CHALLENGE_TITLES: tuple[tuple[str, str], ...] = (
    ("Cloudflare", "just a moment..."),
    ("Cloudflare", "just a moment\u2026"),
    ("Cloudflare", "attention required! | cloudflare"),
    ("Cloudflare", "please wait... | cloudflare"),
    ("Sucuri", "sucuri website firewall - access denied"),
)
BOT_CHALLENGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("Cloudflare", "/cdn-cgi/challenge-platform/"),
    ("Cloudflare", "_cf_chl_opt"),
    ("Cloudflare", 'id="cf-error-details"'),
    ("Imperva", "_incapsula_resource"),
    ("DataDome", "captcha-delivery.com"),
    ("Sucuri", "sucuri website firewall"),
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

# --- template placeholders -------------------------------------------------------------

# Email domains no business receives mail at: documentation names and the defaults website
# templates ship with. `info@mysite.com` is Wix's; every enquiry sent to it is lost.
PLACEHOLDER_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "mysite.com",
        "domain.com",
        "yourdomain.com",
        "yoursite.com",
        "yourwebsite.com",
        "yourcompany.com",
        "yourbusiness.com",
        "company.com",
        "website.com",
    }
)
# The first label of a domain that is a placeholder whatever follows it:
# `yourdomain.co.uk`, `mysite.net`.
PLACEHOLDER_EMAIL_LABELS = frozenset(
    {"example", "mysite", "yourdomain", "yoursite", "yourwebsite", "yourcompany", "yourbusiness"}
)
# `email.com` is a real free-mail provider, so an address there is only a placeholder when
# its local part is one a template would print. `jane.doe@email.com` is somebody.
AMBIGUOUS_EMAIL_DOMAINS = frozenset({"email.com"})
PLACEHOLDER_LOCAL_PARTS = frozenset(
    {"your", "youremail", "yourname", "name", "email", "info", "hello", "contact", "user", "test"}
)

# Text a template prints until someone replaces it. Looked for in the footer (and on the
# line a copyright notice is printed on), because "Company Name" is also an ordinary form
# label in a quote form further up the page.
FOOTER_PLACEHOLDER_PHRASES: tuple[str, ...] = (
    "your company name",
    "your business name",
    "company name",
    "business name",
    "your company",
    "your business",
    "your name here",
    "insert text here",
)
# Placeholder text that is one anywhere on the page.
PAGE_PLACEHOLDER_PHRASES: tuple[str, ...] = ("lorem ipsum",)

# --- JSON-LD ---------------------------------------------------------------------------

# schema.org's whole LocalBusiness subtree (schema.org 26, lowercased), plus three names
# seen on real local-services sites that are not schema.org types but mean the same thing.
# v0.12.0 widened this from 41 entries: once "structured data, but no LocalBusiness" became
# a finding of its own, a real subtype missing here would be a false statement about the
# business, so the list is now the full tree rather than the types we happened to meet.
LOCAL_BUSINESS_TYPES = frozenset(
    {
        "localbusiness",
        # direct subtypes
        "animalshelter",
        "archiveorganization",
        "automotivebusiness",
        "childcare",
        "dentist",
        "drycleaningorlaundry",
        "emergencyservice",
        "employmentagency",
        "entertainmentbusiness",
        "financialservice",
        "foodestablishment",
        "governmentoffice",
        "healthandbeautybusiness",
        "homeandconstructionbusiness",
        "internetcafe",
        "legalservice",
        "library",
        "lodgingbusiness",
        "medicalbusiness",
        "professionalservice",
        "radiostation",
        "realestateagent",
        "recyclingcenter",
        "selfstorage",
        "shoppingcenter",
        "sportsactivitylocation",
        "store",
        "televisionstation",
        "touristinformationcenter",
        "travelagency",
        # automotive
        "autobodyshop",
        "autodealer",
        "autopartsstore",
        "autorental",
        "autorepair",
        "autowash",
        "gasstation",
        "motorcycledealer",
        "motorcyclerepair",
        # emergency
        "firestation",
        "hospital",
        "policestation",
        # entertainment
        "adultentertainment",
        "amusementpark",
        "artgallery",
        "casino",
        "comedyclub",
        "movietheater",
        "nightclub",
        # financial
        "accountingservice",
        "automatedteller",
        "bankorcreditunion",
        "insuranceagency",
        # food
        "bakery",
        "barorpub",
        "brewery",
        "cafeorcoffeeshop",
        "distillery",
        "fastfoodrestaurant",
        "icecreamshop",
        "restaurant",
        "winery",
        # government
        "postoffice",
        # health and beauty
        "beautysalon",
        "dayspa",
        "hairsalon",
        "healthclub",
        "nailsalon",
        "tattooparlor",
        # home and construction
        "electrician",
        "generalcontractor",
        "hvacbusiness",
        "housepainter",
        "locksmith",
        "movingcompany",
        "plumber",
        "roofingcontractor",
        # legal
        "attorney",
        "notary",
        # lodging
        "bedandbreakfast",
        "campground",
        "hostel",
        "hotel",
        "motel",
        "resort",
        "vacationrental",
        # medical
        "communityhealth",
        "dermatology",
        "dietnutrition",
        "medicalclinic",
        "optician",
        "pharmacy",
        "physician",
        "individualphysician",
        "physiciansoffice",
        "covidtestingfacility",
        "veterinarycare",
        # sports
        "bowlingalley",
        "exercisegym",
        "golfcourse",
        "publicswimmingpool",
        "skiresort",
        "sportsclub",
        "stadiumorarena",
        "tenniscomplex",
        # stores
        "bikestore",
        "bookstore",
        "clothingstore",
        "computerstore",
        "conveniencestore",
        "departmentstore",
        "electronicsstore",
        "florist",
        "furniturestore",
        "gardenstore",
        "grocerystore",
        "hardwarestore",
        "hobbyshop",
        "homegoodsstore",
        "jewelrystore",
        "liquorstore",
        "mensclothingstore",
        "mobilephonestore",
        "movierentalstore",
        "musicstore",
        "officeequipmentstore",
        "outletstore",
        "pawnshop",
        "petstore",
        "shoestore",
        "sportinggoodsstore",
        "tireshop",
        "toystore",
        "wholesalestore",
        # not schema.org types, but written by real sites to mean one
        "bar",
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


def schema_type_name(raw: object) -> str | None:
    """A schema.org type as its bare name: `schema.org/Plumber` (as a full https URL) → `Plumber`.

    Handles the three spellings sites use — a full URL (with or without a trailing slash),
    a `schema:` CURIE and the bare name — and keeps the name's own casing. `None` for
    anything that is not a non-empty string.
    """
    if not isinstance(raw, str):
        return None
    value = raw.strip().rstrip("/")
    if value.lower().startswith("schema:"):
        value = value[len("schema:") :]
    name = value.replace("#", "/").split("/")[-1].strip()
    return name or None


def schema_type_names(raw: object) -> list[str]:
    """Every type named by an `@type` / `itemtype` / `typeof` value, a string or a list."""
    values: Sequence[object] = raw if isinstance(raw, list) else [raw]
    names: list[str] = []
    for value in values:
        # `itemtype` and `typeof` may name several types in one space-separated string.
        parts = value.split() if isinstance(value, str) else [value]
        for part in parts:
            name = schema_type_name(part)
            if name is not None and name not in names:
                names.append(name)
    return names


def is_local_business_type(raw: object) -> bool:
    """Whether a JSON-LD `@type` (a string or a list) is LocalBusiness or a subtype."""
    return any(name.lower() in LOCAL_BUSINESS_TYPES for name in schema_type_names(raw))


ALL_SIGNATURE_GROUPS: tuple[tuple[str, tuple[Signature, ...]], ...] = (
    ("booking", BOOKING_SIGNATURES),
    ("chat", CHAT_SIGNATURES),
    ("tech", TECH_SIGNATURES),
    ("social", SOCIAL_PLATFORMS),
)
