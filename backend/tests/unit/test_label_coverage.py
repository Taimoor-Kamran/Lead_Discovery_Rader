"""Every code the API can put on a screen has human wording waiting for it.

v0.9.0's rule is that codes are read, not shown. A label table can only keep that promise
if it covers the codes the backend actually emits, and the backend is the only place that
knows what those are — the adapter registry, the platform signatures and the AI schema are
Python. So the guard lives here, reads `frontend/src/lib/labels.ts`, and fails naming the
missing code.

This is deliberately about *coverage*, not about spelling: the rule "no raw code reaches the
screen" is asserted by rendering pages in `frontend/src/test/labels.test.tsx`. Together they
say: every code has a label, and no code gets rendered instead of its label.
"""

import re
from pathlib import Path

import pytest

from app.modules.adapters import registry
from app.modules.adapters.demo_fixture import DemoFixtureAdapter
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.ai.models import ClassificationStatus
from app.modules.ai.schema import BuyingIntent
from app.modules.audit_web.fingerprints import SOCIAL_PLATFORMS
from app.modules.audit_web.models import AuditStatus
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.opportunities.catalogue import SERVICES
from app.modules.opportunities.models import OpportunitySource

# tests/unit/… → backend/ → the repository root, where `frontend/` sits next to `backend/`.
LABELS_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "labels.ts"

# `key: "Label"` or `"key-with-dashes": "Label"` inside one exported table.
ENTRY = re.compile(r'^\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*:\s*"', re.MULTILINE)


def labels(table: str) -> set[str]:
    """The keys of one `export const <table>: Record<string, string> = { … }` block."""
    source = LABELS_TS.read_text(encoding="utf-8")
    start = source.index(f"export const {table}: Record<string, string> = {{")
    body = source[start : source.index("\n};", start)]
    return {quoted or bare for quoted, bare in ENTRY.findall(body)}


def registered_source_names() -> set[str]:
    """Every name that can end up in `sources.name`, whatever the environment.

    `registry.names()` leaves the demo fixture out outside development, and the suite runs
    under `ci`, so the two built-in adapter classes are named directly. Anything else the
    registry or the service list knows about is picked up automatically — which is the point:
    a new adapter fails this test until it has a label.
    """
    names = set(registry.names())
    names.update({GooglePlacesAdapter.name, DemoFixtureAdapter.name})
    names.update(spec.name for spec in registry.service_sources())
    return names


def test_every_registered_source_has_a_label() -> None:
    missing = sorted(registered_source_names() - labels("SOURCE_NAME_LABELS"))

    assert not missing, (
        f"{missing} would render as a raw code. Add wording to SOURCE_NAME_LABELS in "
        f"{LABELS_TS.name} before shipping the source."
    )


def test_no_source_label_is_left_over() -> None:
    """A label for a source that no longer exists is dead wording; delete it with the source."""
    stale = sorted(labels("SOURCE_NAME_LABELS") - registered_source_names())

    assert not stale, f"{stale} have labels but are not registered sources"


def test_every_social_platform_the_audit_records_has_a_label() -> None:
    codes = {platform.key for platform in SOCIAL_PLATFORMS}
    assert not sorted(codes - labels("SOCIAL_PLATFORM_LABELS"))
    assert not sorted(labels("SOCIAL_PLATFORM_LABELS") - codes)


@pytest.mark.parametrize(
    ("table", "codes"),
    [
        ("BUYING_INTENT_LABELS", set(BuyingIntent.__args__)),  # type: ignore[attr-defined]
        ("AI_STATUS_LABELS", {status.value for status in ClassificationStatus}),
        ("AUDIT_STATUS_LABELS", {status.value for status in AuditStatus}),
        ("WEBSITE_KIND_LABELS", {kind.value for kind in WebsiteKind}),
        ("BUSINESS_STATUS_LABELS", {status.value for status in BusinessStatus}),
        ("SOURCE_LABELS", {source.value for source in OpportunitySource}),
        ("SERVICE_LABELS", set(SERVICES)),
    ],
)
def test_every_enum_value_the_api_emits_has_a_label(table: str, codes: set[str]) -> None:
    missing = sorted(codes - labels(table))

    assert not missing, f"{missing} would render as a raw code; add them to {table}"
