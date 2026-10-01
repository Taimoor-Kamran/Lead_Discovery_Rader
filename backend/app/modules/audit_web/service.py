"""Auditing one business, and reading audits back.

The shape of one audit:

1. decide what to fetch. A business with no website, or only a social profile, is audited
   with **zero network calls** — the finding is about the listing, not about a page;
2. ask `robots.txt` first. Disallowed means the homepage is never requested at all;
3. fetch the homepage through the SSRF guard, once;
4. run the deterministic checks, compare the page with the business's own listing, then
   ask PageSpeed for the mobile numbers;
5. turn the gaps into findings, each carrying the text and URL it came from.

A request that gets no answer at all is not taken at its word (v0.12.0, item 3a). Our own
connectivity is checked first — a laptop that slept mid-run must not record a prospect's
site as down — and the site is asked once more before it is called unreachable.

Every step that can fail produces a *status* rather than an exception, because an audit
that did not work is still information about that business. The only thing that raises is
a bug in our own code, and the worker catches that per business too.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.connectivity import ConnectivityProbe, ProbeResult, build_probe
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.core.safe_fetch import (
    FetchOutcome,
    RobotsDecision,
    SafeFetcher,
    UnsafeUrlError,
    build_fetcher,
)
from app.modules.audit_web import checks as checks_module
from app.modules.audit_web import findings as findings_module
from app.modules.audit_web import listing as listing_module
from app.modules.audit_web.models import (
    AUDIT_LOGIC_VERSION,
    RULES_VERSION,
    AuditStatus,
    WebsiteAudit,
)
from app.modules.audit_web.psi import (
    PageSpeedClient,
    PageSpeedUnavailableError,
    build_psi_client,
)
from app.modules.audit_web.schemas import WebsiteAuditDetail, WebsiteAuditSummary
from app.modules.businesses.models import Business, BusinessFieldValue
from app.modules.discovery.models import DiscoveredRecord
from app.modules.jobs.models import JobRun as JobRunType
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind

logger = get_logger("app.audit_web")

# The two kinds of website worth fetching. A social profile is recorded, never fetched —
# the blueprint's hard rule — and a business with no website has nothing to fetch.
AUDITABLE_WEBSITE_KINDS = frozenset({WebsiteKind.own_site, WebsiteKind.builder_subdomain})
# Failures where no answer came back at all — which is also what our own outage looks like.
# A certificate that did not verify, or a URL the guard refused, is an answer: never re-checked.
NO_ANSWER_KINDS = frozenset({"connect", "timeout", "network"})


@dataclass
class AuditTools:
    """The outside things an audit needs. Injected so a test can drive each one.

    `connectivity` is `None` in tests unless one is given: then a failure is still
    re-checked, but nothing can say our own network was down.
    """

    fetcher: SafeFetcher
    psi: PageSpeedClient
    settings: Settings = field(default_factory=get_settings)
    connectivity: ConnectivityProbe | None = None

    @classmethod
    def build(cls, *, job_run_id: uuid.UUID | None = None) -> "AuditTools":
        settings = get_settings()
        return cls(
            fetcher=build_fetcher(settings=settings),
            psi=build_psi_client(job_run_id=job_run_id, settings=settings),
            settings=settings,
            connectivity=build_probe(settings),
        )


# --- auditing one business --------------------------------------------------------------


def audit_business(
    session: Session,
    business: Business,
    *,
    tools: AuditTools,
    job_run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> WebsiteAudit:
    """Audit one business's homepage and store the result. Never raises for a bad site."""
    settings = tools.settings
    started = now or datetime.now(UTC)
    context = findings_module.FindingContext(
        industry=business.industry,
        website_kind=business.website_kind,
        website=business.website,
        booking_industries=frozenset(item.lower() for item in settings.audit_booking_industries),
        slow_mobile_score=settings.audit_slow_mobile_score,
        stale_copyright_years=settings.audit_stale_copyright_years,
        thin_content_words=settings.audit_thin_content_words,
        quality_score_threshold=settings.audit_quality_score_threshold,
        quality_score_medium_below=settings.audit_quality_score_medium_below,
        now=started,
        business_name=business.display_name,
    )

    if business.website_kind not in AUDITABLE_WEBSITE_KINDS or not business.website:
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=business.website or "",
            status=AuditStatus.skipped,
            findings=findings_module.for_missing_website(
                context, business_label=business.display_name
            ),
            started_at=started,
            settings=settings,
        )

    url = business.website
    try:
        robots, outage = _with_recheck(tools, url, lambda: tools.fetcher.robots(url), _no_robots)
    except UnsafeUrlError as exc:
        return _refused(session, business, job_run_id, url, exc, started, settings)
    if outage is not None:
        return _our_outage(session, business, job_run_id, url, outage, started, settings)

    # A certificate that does not verify, or a host that never answers, shows up on the
    # robots request — before the homepage is asked for. Neither is a robots decision, so
    # each is recorded as what it is (`tls_invalid`, `unreachable`) and the homepage is
    # still never requested. A timeout or a 5xx *is* left to the robots policy: the server
    # is there, it just would not tell us the rules.
    if not robots.tls_valid or robots.error_kind == "connect":
        checks = checks_module.fetch_checks(
            _failed_outcome(url, robots.reason, robots.error_kind, tls_valid=robots.tls_valid)
        )
        if robots.tls_valid and not _load_failed_before(session, business):
            return _unanswered(session, business, job_run_id, url, checks, started, settings)
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=url,
            status=AuditStatus.unreachable,
            checks=checks,
            findings=findings_module.for_unreachable(
                checks, url, note=_recheck_note(tools, url) if robots.tls_valid else None
            ),
            started_at=started,
            settings=settings,
        )

    if not robots.allowed:
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=url,
            status=AuditStatus.robots_blocked,
            findings=findings_module.for_robots_blocked(robots.reason, robots.robots_url),
            started_at=started,
            settings=settings,
        )

    try:
        outcome, outage = _with_recheck(tools, url, lambda: tools.fetcher.fetch(url), _no_page)
    except UnsafeUrlError as exc:
        return _refused(session, business, job_run_id, url, exc, started, settings)
    if outage is not None:
        return _our_outage(session, business, job_run_id, url, outage, started, settings)

    checks = checks_module.fetch_checks(outcome)
    final_url = outcome.final_url or url

    challenge = checks_module.bot_challenge(outcome)
    if challenge is not None:
        return _challenged(
            session, business, job_run_id, url, outcome, challenge, started, settings
        )

    refused = checks_module.not_readable(outcome) if outcome.reachable else None
    if refused is not None:
        return _not_readable(
            session, business, job_run_id, url, outcome, refused, started, settings
        )

    if not outcome.reachable:
        if outcome.tls_valid and not _load_failed_before(session, business):
            return _unanswered(session, business, job_run_id, url, checks, started, settings)
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=url,
            final_url=final_url,
            status=AuditStatus.unreachable,
            http_status=outcome.status_code,
            checks=checks,
            findings=findings_module.for_unreachable(
                checks, final_url, note=_recheck_note(tools, url) if outcome.tls_valid else None
            ),
            html_sha256=outcome.html_sha256,
            started_at=started,
            settings=settings,
        )

    html, page_text = checks_module.analyse_html(
        outcome, now=started, page_text_limit=settings.audit_page_text_max_chars
    )
    checks.update(html)
    if html:
        checks["listing_comparison"] = listing_module.compare(checks, _listing(business))
        booking = checks.get("booking")
        if booking is not None and booking.method in checks_module.BOOKING_TO_FOLLOW:
            checks["booking"] = _follow_booking_link(tools, booking, outcome, context)
    psi, psi_error = _measure(tools.psi, final_url)
    if psi_error is not None:
        checks["psi_error"] = checks_module.CheckResult(
            psi_error, evidence_text=psi_error, evidence_url=final_url
        )

    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        final_url=final_url,
        status=AuditStatus.done,
        http_status=outcome.status_code,
        checks=checks,
        psi=psi,
        findings=findings_module.for_page(checks, psi, context),
        page_text=page_text,
        html_sha256=outcome.html_sha256,
        started_at=started,
        settings=settings,
    )


def _follow_booking_link(
    tools: AuditTools,
    check: checks_module.CheckResult,
    homepage: FetchOutcome,
    context: findings_module.FindingContext,
) -> checks_module.CheckResult:
    """Fetch the one page a booking call to action or path points at, and read it (v0.12.1).

    A link's text is not evidence of what is behind it: ATX Electrical's "Schedule Now"
    goes to a contact form with no date or time on it. So the target is fetched once,
    through `safe_fetch` (robots, SSRF guard, per-host throttle), and only what is on it
    decides: a booking flow there is `verified_target`; a page that only leads further (a
    link with a booking word, a choice of locations) is `unverified_multi_hop`; none of
    these is `False`, with the call to action and the target named in the evidence; and
    anything that stops us reading the page is `unverified`. Both unverified outcomes
    carry a null value and draw no finding. One page, never a crawl.
    """
    seen = str(check.value).split(": ", 1)[-1]
    target = check.target_url
    home = homepage.final_url or homepage.url

    def unverified(why: str) -> checks_module.CheckResult:
        return checks_module.CheckResult(
            None,
            evidence_text=f"'{seen}' could not be checked: {why}. {check.evidence_text or ''}",
            evidence_url=check.evidence_url,
            method=checks_module.BOOKING_UNVERIFIED,
            target_url=target,
        )

    if (context.industry or "").lower() not in context.booking_industries:
        return unverified("not followed, online booking is not expected in this industry")
    if target is None:
        return unverified("it has no link to a page")

    if checks_module.same_page(target, home):
        # `_booking` already skips a link back to the page; never spend a fetch on one.
        return unverified("it links back to the page being audited")
    try:
        robots = tools.fetcher.robots(target)
        if not robots.allowed:
            return unverified(robots.reason)
        page = tools.fetcher.fetch(target)
    except UnsafeUrlError as exc:
        return unverified(f"the link was refused ({exc.reason})")
    if not page.reachable or page.status_code is None:
        return unverified(f"{target} did not answer ({page.error_kind or 'no response'})")
    if not 200 <= page.status_code < 300:
        return unverified(f"{target} answered HTTP {page.status_code}")
    if page.text is None:
        return unverified(f"{target} is not a page that can be read")
    if checks_module.bot_challenge(page) is not None:
        return unverified(f"{target} answered with a bot-protection challenge")

    flow = checks_module.booking_target_flow(page)
    if flow is not None:
        return checks_module.CheckResult(
            check.value,
            evidence_text=f"'{seen}' links to {target}; {flow}",
            evidence_url=check.evidence_url,
            method=checks_module.BOOKING_VERIFIED_TARGET,
            target_url=target,
        )
    onward = checks_module.booking_onward_hop(page)
    if onward is not None:
        return checks_module.CheckResult(
            None,
            evidence_text=(
                f"'{seen}' links to {target}, which shows no scheduler itself but leads "
                f"further ({onward}); only one page is followed, so booking is not judged"
            ),
            evidence_url=check.evidence_url,
            method=checks_module.BOOKING_UNVERIFIED_MULTI_HOP,
            target_url=target,
        )
    if checks_module.looks_script_built(page):
        return unverified(f"{target} is assembled by scripts this audit does not run")
    return checks_module.CheckResult(
        False,
        evidence_text=(
            f"'{seen}' links to {target}, which shows no date or time input, no booking "
            "vendor and no onward booking link"
        ),
        evidence_url=check.evidence_url,
        method=check.method,
        target_url=target,
    )


def record_failure(
    session: Session,
    business: Business,
    *,
    reason: str,
    job_run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> WebsiteAudit:
    """Store a `failed` audit for a business whose audit hit a bug in our own code.

    Deliberately separate from the statuses above: `unreachable` is a fact about the
    website, `failed` is a fact about us, and a salesperson must be able to tell them
    apart.
    """
    settings = get_settings()
    audit = _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=business.website or "",
        status=AuditStatus.failed,
        checks={
            "error": checks_module.CheckResult(
                reason, evidence_text=reason, evidence_url=business.website
            )
        },
        findings=[],
        started_at=now or datetime.now(UTC),
        settings=settings,
    )
    logger.warning(
        "a business audit failed",
        extra={"business_id": str(business.id), "reason": reason},
    )
    return audit


def _listing(business: Business) -> listing_module.Listing:
    """The business record's own facts, as the listing comparison reads them."""
    return listing_module.Listing(
        name=business.display_name,
        phone_e164=business.phone_e164,
        address_line1=business.address_line1,
        address_line2=business.address_line2,
        city=business.city,
        state=business.state,
        postal_code=business.postal_code,
        country=business.country,
        website=business.website,
    )


# --- no answer: their site, or our network? ---------------------------------------------


def _no_robots(robots: RobotsDecision) -> bool:
    return robots.tls_valid and robots.error_kind in NO_ANSWER_KINDS


def _no_page(outcome: FetchOutcome) -> bool:
    if not outcome.tls_valid:
        return False
    # A bot-protection page is an answer, not a silence: re-asking gets the same page.
    if checks_module.bot_challenge(outcome) is not None:
        return False
    if not outcome.reachable:
        return outcome.error_kind in NO_ANSWER_KINDS
    return (outcome.status_code or 0) >= 500


def _with_recheck[T](
    tools: AuditTools, url: str, attempt: Callable[[], T], no_answer: Callable[[T], bool]
) -> tuple[T, ProbeResult | None]:
    """One request, re-checked once if nothing answered. Returns `(result, outage)`.

    `outage` is set when our own connectivity check failed at either attempt: the site was
    never fairly asked, so nothing may be concluded about it. Otherwise the site gets a
    second chance `AUDIT_UNREACHABLE_RECHECK_SECONDS` later, and only a second failure
    stands. A fixture on disk is deterministic and is neither re-checked nor probed.
    """
    result = attempt()
    if not no_answer(result) or not tools.fetcher.uses_network(url):
        return result, None
    outage = _outage(tools)
    if outage is not None:
        return result, outage
    logger.info("no answer from a site; checking once more", extra={"url": url})
    tools.fetcher.sleeper(tools.settings.audit_unreachable_recheck_seconds)
    result = attempt()
    if no_answer(result):
        return result, _outage(tools)
    return result, None


def _outage(tools: AuditTools) -> ProbeResult | None:
    """The probe's answer when our own network is down; `None` when it is up or unknown."""
    if tools.connectivity is None:
        return None
    probe = tools.connectivity.check()
    return None if probe.online else probe


def _recheck_note(tools: AuditTools, url: str) -> str | None:
    """What an unreachable finding's evidence adds about how hard we tried."""
    if not tools.fetcher.uses_network(url):
        return None
    seconds = tools.settings.audit_unreachable_recheck_seconds
    network = (
        "while our own connectivity check succeeded"
        if tools.connectivity is not None
        else "(our own connectivity was not checked)"
    )
    return f"tried twice, {seconds:g} s apart, {network}"


def _challenged(
    session: Session,
    business: Business,
    job_run_id: uuid.UUID | None,
    url: str,
    outcome: FetchOutcome,
    challenge: checks_module.CheckResult,
    started: datetime,
    settings: Settings,
) -> WebsiteAudit:
    """The site served a bot-protection challenge instead of its homepage (item 3b).

    Nothing about the business's site was read, so nothing is reported: no page findings,
    no PageSpeed call, no page text, and not even the listing's own `few_reviews`, so the
    audit cannot become an opportunity. The status says what happened, and the check
    carries the challenge page's status and title as evidence.
    """
    checks = checks_module.fetch_checks(outcome)
    checks["bot_challenge"] = challenge
    logger.info(
        "a site answered with a bot-protection challenge",
        extra={"business_id": str(business.id), "vendor": challenge.value},
    )
    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        final_url=outcome.final_url or url,
        status=AuditStatus.bot_challenge,
        http_status=outcome.status_code,
        checks=checks,
        findings=[],
        html_sha256=outcome.html_sha256,
        started_at=started,
        settings=settings,
        listing_findings=False,
    )


def _load_failed_before(session: Session, business: Business) -> bool:
    """Whether the business's previous audit also got no page (v0.12.0, run 4).

    A site that loaded on its last audit and gives no answer now is most often limiting how
    often we may ask — Mister Sparky of Austin loaded twice in a day, then timed out twice
    — and calling it offline would tell a rep something false. So no answer is `unreachable`
    only the second time in a row. Our own `failed` audits say nothing about the site and
    are looked past; "no page" is an `unreachable` audit or a `not_readable` one that got
    no HTTP answer at all.
    """
    for audit in _recent_audits(session, business.id, limit=10):
        if audit.status is AuditStatus.failed:
            continue
        return audit.status is AuditStatus.unreachable or (
            audit.status is AuditStatus.not_readable and audit.http_status is None
        )
    return False


def _unanswered(
    session: Session,
    business: Business,
    job_run_id: uuid.UUID | None,
    url: str,
    checks: checks_module.Checks,
    started: datetime,
    settings: Settings,
) -> WebsiteAudit:
    """No answer, twice, with our network up — but the page loaded last time: not readable."""
    reachable = checks.get("reachable")
    reason = reachable.evidence_text if reachable is not None else f"No HTTP answer from {url}"
    checks["not_readable"] = checks_module.CheckResult(
        None,
        evidence_text=(
            f"{reason}. The previous audit of this business loaded or has not yet tried the "
            "page, so this is recorded as not readable, not as the site being offline"
        ),
        evidence_url=url,
    )
    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        status=AuditStatus.not_readable,
        checks=checks,
        findings=[],
        started_at=started,
        settings=settings,
        listing_findings=False,
    )


def _not_readable(
    session: Session,
    business: Business,
    job_run_id: uuid.UUID | None,
    url: str,
    outcome: FetchOutcome,
    refused: checks_module.CheckResult,
    started: datetime,
    settings: Settings,
) -> WebsiteAudit:
    """The homepage answered with a non-2xx status (v0.12.0, run 2).

    A "403 Forbidden" error page was audited as the homepage in production and reported as a
    template title, thin content and more. Now nothing in the response is read as the page:
    no page findings, no PageSpeed call, no page text. What does stand are the findings that
    come from the URLs alone — the listing's website against the address we were sent to.
    """
    checks = checks_module.fetch_checks(outcome)
    checks["not_readable"] = refused
    checks["listing_comparison"] = checks_module.CheckResult(
        {"website": listing_module.compare_website(checks, _listing(business))},
        evidence_text="Only the website address is compared: the page was not readable",
        evidence_url=outcome.final_url or url,
    )
    logger.info(
        "a homepage answered with a non-2xx status",
        extra={"business_id": str(business.id), "status": refused.value},
    )
    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        final_url=outcome.final_url or url,
        status=AuditStatus.not_readable,
        http_status=outcome.status_code,
        checks=checks,
        findings=findings_module.for_not_readable(checks),
        html_sha256=outcome.html_sha256,
        started_at=started,
        settings=settings,
        listing_findings=False,
    )


def _our_outage(
    session: Session,
    business: Business,
    job_run_id: uuid.UUID | None,
    url: str,
    probe: ProbeResult,
    started: datetime,
    settings: Settings,
) -> WebsiteAudit:
    """Our network was down, so the site was never fairly asked. A fact about us: `failed`.

    No finding is made about the site, and `failed` is due again after
    AUDIT_FAILED_RETRY_HOURS rather than backing off like a site that is really down.
    """
    reason = f"Our own network was unavailable ({probe.detail}), so {url} was not judged"
    logger.warning(
        "an audit hit our own network outage",
        extra={"business_id": str(business.id), "detail": probe.detail},
    )
    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        status=AuditStatus.failed,
        checks={
            "error": checks_module.CheckResult(reason, evidence_text=reason, evidence_url=url),
            "network_available": checks_module.CheckResult(
                False, evidence_text=probe.detail, evidence_url=url
            ),
        },
        findings=[],
        started_at=started,
        settings=settings,
    )


def _measure(psi: PageSpeedClient, url: str) -> tuple[dict[str, Any] | None, str | None]:
    """PageSpeed, or the reason there is none. Never fails the audit around it."""
    try:
        return psi.analyse(url).as_dict(), None
    except PageSpeedUnavailableError as exc:
        logger.info("pagespeed was unavailable", extra={"url": url, "reason": exc.reason})
        return None, exc.reason


def _listing_url(session: Session, business: Business) -> str | None:
    """The source page of the record whose review count the business shows."""
    if business.user_rating_count is None:
        return None
    return session.scalar(
        select(DiscoveredRecord.source_url)
        .join(BusinessFieldValue, BusinessFieldValue.discovered_record_id == DiscoveredRecord.id)
        .where(
            BusinessFieldValue.business_id == business.id,
            BusinessFieldValue.field == "user_rating_count",
            BusinessFieldValue.value == str(business.user_rating_count),
        )
        .order_by(BusinessFieldValue.observed_at.desc())
        .limit(1)
    )


def _score(psi: dict[str, Any] | None, key: str) -> int | None:
    value = (psi or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _failed_outcome(
    url: str, reason: str, kind: str | None, *, tls_valid: bool = True
) -> FetchOutcome:
    """A fetch that never happened, in the shape the checks read."""
    return FetchOutcome(url=url, final_url=url, tls_valid=tls_valid, error=reason, error_kind=kind)


def _refused(
    session: Session,
    business: Business,
    job_run_id: uuid.UUID | None,
    url: str,
    exc: UnsafeUrlError,
    started: datetime,
    settings: Settings,
) -> WebsiteAudit:
    """A URL the SSRF guard would not request. Recorded as unreachable, with the reason."""
    checks = checks_module.fetch_checks(_failed_outcome(url, exc.reason, "refused"))
    logger.info(
        "an audit target was refused by the fetch guard",
        extra={"business_id": str(business.id), "reason": exc.reason},
    )
    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        status=AuditStatus.unreachable,
        checks=checks,
        findings=findings_module.for_unreachable(checks, url),
        started_at=started,
        settings=settings,
    )


def _store(
    session: Session,
    *,
    business: Business,
    job_run_id: uuid.UUID | None,
    url_audited: str,
    status: AuditStatus,
    findings: list[findings_module.Finding],
    started_at: datetime,
    settings: Settings,
    final_url: str | None = None,
    http_status: int | None = None,
    checks: checks_module.Checks | None = None,
    psi: dict[str, Any] | None = None,
    page_text: str | None = None,
    html_sha256: str | None = None,
    listing_findings: bool = True,
) -> WebsiteAudit:
    resolved_checks = checks or {}
    tech_stack = checks_module.value_of(resolved_checks, "tech_stack") or {}
    ttl_days = settings.audit_content_ttl_days
    if listing_findings:
        findings = [
            *findings,
            *findings_module.for_listing(
                business.user_rating_count,
                few_reviews=settings.places_few_reviews,
                listing_url=_listing_url(session, business),
            ),
        ]
    audit = WebsiteAudit(
        business_id=business.id,
        job_run_id=job_run_id,
        url_audited=url_audited,
        final_url=final_url,
        status=status,
        http_status=http_status,
        checks=checks_module.as_payload(resolved_checks),
        psi=psi,
        accessibility_score=_score(psi, "accessibility_score"),
        best_practices_score=_score(psi, "best_practices_score"),
        tech_stack=tech_stack,
        findings=findings_module.as_payload(findings),
        page_text=page_text,
        html_sha256=html_sha256,
        rules_version=RULES_VERSION,
        audit_logic_version=AUDIT_LOGIC_VERSION,
        content_expires_at=(
            started_at + timedelta(days=ttl_days) if ttl_days > 0 and page_text else None
        ),
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )
    session.add(audit)
    session.flush()
    logger.info(
        "website audited",
        extra={
            "business_id": str(business.id),
            "website_audit_id": str(audit.id),
            "status": status.value,
            "findings": audit.finding_codes,
        },
    )
    return audit


# --- enqueueing ---------------------------------------------------------------------------

# Imported by name rather than from `jobs.service`, which imports this module back.
RESOLUTION_JOB_KIND = "resolution"


def enqueue_audits_for_run(
    session: Session,
    resolution_run_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    dispatch: bool = True,
) -> JobRunType:
    """Queue an audit run for the businesses one resolution run touched.

    Only a resolution run can be audited: it is what says which businesses just changed.
    Asking for anything else is a 422 rather than a run that would find nothing.
    """
    from app.modules.jobs.service import AUDIT_JOB_KIND, enqueue_run, get_job_run

    parent = get_job_run(session, resolution_run_id)
    if parent.kind != RESOLUTION_JOB_KIND:
        raise ValidationFailedError(
            "Only a resolution run's businesses can be audited",
            details={"job_run_id": str(resolution_run_id), "kind": parent.kind},
        )
    return enqueue_run(
        session,
        search_job_id=parent.search_job_id,
        kind=AUDIT_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        dispatch=dispatch,
        params={"parent_run_id": str(parent.id)},
    )


def enqueue_audit_for_business(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> JobRunType:
    """Queue a fresh audit of one business now, however recently it was last audited."""
    from app.modules.businesses.service import get_business
    from app.modules.jobs.service import AUDIT_JOB_KIND, enqueue_run

    business = get_business(session, business_id)
    return enqueue_run(
        session,
        search_job_id=None,
        kind=AUDIT_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        params={"business_id": str(business.id)},
    )


# --- choosing what to audit -------------------------------------------------------------


def businesses_for_run(
    session: Session, resolution_run_id: uuid.UUID, *, now: datetime | None = None
) -> list[Business]:
    """The businesses one resolution run touched, minus those audited recently enough.

    "Touched" means linked or created while resolving the discovery run this resolution
    run was pointed at, which is exactly the set of businesses whose facts just changed.
    """
    from app.modules.discovery.models import DiscoveredRecord, RecordSighting

    resolution_run = session.get(JobRunType, resolution_run_id)
    if resolution_run is None:
        raise NotFoundError("Job run not found", details={"job_run_id": str(resolution_run_id)})
    discovery_run_id = (resolution_run.params or {}).get("parent_run_id")
    if not discovery_run_id:
        raise ValidationFailedError(
            "That resolution run does not say which discovery run it resolved",
            details={"job_run_id": str(resolution_run_id)},
        )

    business_ids = list(
        session.scalars(
            select(DiscoveredRecord.business_id)
            .join(RecordSighting, RecordSighting.discovered_record_id == DiscoveredRecord.id)
            .where(
                RecordSighting.job_run_id == uuid.UUID(str(discovery_run_id)),
                DiscoveredRecord.business_id.is_not(None),
            )
            .distinct()
        )
    )
    if not business_ids:
        return []

    businesses = list(
        session.scalars(
            select(Business)
            .where(
                Business.id.in_(business_ids),
                # A business that has closed for good is not a lead, so it is not audited.
                Business.business_status != BusinessStatus.closed_permanently,
            )
            .order_by(Business.created_at.asc(), Business.id.asc())
        )
    )
    return [b for b in businesses if needs_audit(session, b, now=now)]


def needs_audit(session: Session, business: Business, *, now: datetime | None = None) -> bool:
    """Whether the newest audit for this business is missing or too old to trust.

    How old is too old depends on what the newest audit says (spec v0.11.1). A `failed`
    audit is a fault on our side and says nothing about the site, so it is due again after
    AUDIT_FAILED_RETRY_HOURS; before, one timeout hid a business for a month. An
    `unreachable` one backs off through AUDIT_UNREACHABLE_BACKOFF_DAYS, a step per
    consecutive unreachable audit, so a site that was briefly down is looked at again
    soon and one that is gone settles into the normal AUDIT_MAX_AGE_DAYS. A
    `bot_challenge` or `not_readable` audit waits AUDIT_BOT_CHALLENGE_RETRY_DAYS (v0.12.0): a
    site that refused us once rarely changes its mind within hours. Everything else
    keeps AUDIT_MAX_AGE_DAYS.

    Before any of that: an audit written by older check logic (`audit_logic_version` below
    AUDIT_LOGIC_VERSION, v0.12.0) is due now, whatever its age or status. It concluded
    things the current code no longer would.
    """
    settings = get_settings()
    if settings.audit_max_age_days <= 0:
        return True
    backoff = settings.audit_unreachable_backoff_days
    recent = _recent_audits(session, business.id, limit=len(backoff) + 1)
    if not recent:
        return True
    latest = recent[0]
    if latest.audit_logic_version < AUDIT_LOGIC_VERSION:
        return True
    if latest.status is AuditStatus.failed:
        wait = timedelta(hours=settings.audit_failed_retry_hours)
    elif latest.status in (AuditStatus.bot_challenge, AuditStatus.not_readable):
        wait = timedelta(days=settings.audit_bot_challenge_retry_days)
    elif latest.status is AuditStatus.unreachable:
        streak = next(
            (i for i, audit in enumerate(recent) if audit.status is not AuditStatus.unreachable),
            len(recent),
        )
        wait = (
            timedelta(days=backoff[streak - 1])
            if streak <= len(backoff)
            else timedelta(days=settings.audit_max_age_days)
        )
    else:
        wait = timedelta(days=settings.audit_max_age_days)
    return latest.created_at < (now or datetime.now(UTC)) - wait


def _recent_audits(session: Session, business_id: uuid.UUID, *, limit: int) -> list[WebsiteAudit]:
    return list(
        session.scalars(
            select(WebsiteAudit)
            .where(WebsiteAudit.business_id == business_id)
            .order_by(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc())
            .limit(limit)
        )
    )


# --- reads --------------------------------------------------------------------------------


def get_audit(session: Session, audit_id: uuid.UUID) -> WebsiteAudit:
    audit = session.get(WebsiteAudit, audit_id)
    if audit is None:
        raise NotFoundError("Website audit not found", details={"website_audit_id": str(audit_id)})
    return audit


def latest_audit(session: Session, business_id: uuid.UUID) -> WebsiteAudit | None:
    return session.scalars(
        select(WebsiteAudit)
        .where(WebsiteAudit.business_id == business_id)
        .order_by(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc())
        .limit(1)
    ).first()


def latest_audits(session: Session, business_ids: list[uuid.UUID]) -> dict[uuid.UUID, WebsiteAudit]:
    """The newest audit per business, in one query. Used by the business list view."""
    if not business_ids:
        return {}
    ranked = _latest_audit_subquery()
    rows = session.scalars(
        select(WebsiteAudit)
        .join(ranked, ranked.c.id == WebsiteAudit.id)
        .where(WebsiteAudit.business_id.in_(business_ids))
    )
    return {row.business_id: row for row in rows}


def _latest_audit_subquery() -> Any:
    """`(id, business_id, status, findings)` of the newest audit of each business."""
    ranked = select(
        WebsiteAudit.id,
        WebsiteAudit.business_id,
        WebsiteAudit.status,
        WebsiteAudit.findings,
        func.row_number()
        .over(
            partition_by=WebsiteAudit.business_id,
            order_by=(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc()),
        )
        .label("rank"),
    ).subquery()
    return select(ranked).where(ranked.c.rank == 1).subquery()


def apply_audit_filters(
    stmt: Select[tuple[Business]],
    *,
    finding_codes: list[str] | None,
    audit_status: AuditStatus | None,
) -> Select[tuple[Business]]:
    """Narrow a business query by what its **newest** audit found.

    The filters combine with AND: `finding=no_https&finding=no_online_booking` returns
    only businesses whose latest audit found both.
    """
    if not finding_codes and audit_status is None:
        return stmt
    latest = _latest_audit_subquery()
    stmt = stmt.join(latest, latest.c.business_id == Business.id)
    if audit_status is not None:
        stmt = stmt.where(latest.c.status == audit_status)
    for code in finding_codes or []:
        # A GIN containment query, which is what `ix_website_audits_findings` is for.
        stmt = stmt.where(latest.c.findings.contains([{"code": code}]))
    return stmt


def list_audits_for_business(
    session: Session,
    business_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[WebsiteAuditSummary]:
    """One business's audit history, newest first."""
    stmt = (
        select(WebsiteAudit)
        .where(WebsiteAudit.business_id == business_id)
        .order_by(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc())
        .limit(limit + 1)
    )
    stmt = apply_cursor(stmt, WebsiteAudit.created_at, WebsiteAudit.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[WebsiteAuditSummary](
        items=[summarize(row) for row in rows], next_cursor=next_cursor
    )


def summarize(audit: WebsiteAudit) -> WebsiteAuditSummary:
    return WebsiteAuditSummary(
        id=audit.id,
        business_id=audit.business_id,
        job_run_id=audit.job_run_id,
        url_audited=audit.url_audited,
        final_url=audit.final_url,
        status=audit.status,
        http_status=audit.http_status,
        finding_codes=audit.finding_codes,
        rules_version=audit.rules_version,
        audit_logic_version=audit.audit_logic_version,
        accessibility_score=audit.accessibility_score,
        best_practices_score=audit.best_practices_score,
        started_at=audit.started_at,
        finished_at=audit.finished_at,
        created_at=audit.created_at,
    )


def detail(audit: WebsiteAudit, *, include_page_text: bool) -> WebsiteAuditDetail:
    """The whole audit. `page_text` is only for the roles allowed to read it."""
    return WebsiteAuditDetail(
        **summarize(audit).model_dump(),
        checks=audit.checks or {},
        psi=audit.psi,
        tech_stack=audit.tech_stack or {},
        findings=audit.findings or [],
        page_text=audit.page_text if include_page_text else None,
        page_text_hidden=bool(audit.page_text) and not include_page_text,
        html_sha256=audit.html_sha256,
        content_expires_at=audit.content_expires_at,
        purged_at=audit.purged_at,
    )


# --- retention ----------------------------------------------------------------------------


def purge_expired_page_text(session: Session, *, now: datetime | None = None) -> int:
    """Null the stored page text of audits past their retention window.

    Only `page_text` goes. The checks, the findings and their evidence snippets are our
    own observations with their own provenance, and they are what a salesperson works
    from — so they stay, and the audit row stays alongside them.
    """
    moment = now or datetime.now(UTC)
    result = session.execute(
        update(WebsiteAudit)
        .where(
            WebsiteAudit.content_expires_at.is_not(None),
            WebsiteAudit.content_expires_at < moment,
            WebsiteAudit.page_text.is_not(None),
        )
        .values(page_text=None, purged_at=moment)
    )
    purged = int(getattr(result, "rowcount", 0) or 0)
    if purged:
        logger.info("purged expired audit page text", extra={"audits": purged})
    return purged
