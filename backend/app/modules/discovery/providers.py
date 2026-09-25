"""The third-party data providers a source says must be shown with its results (v0.11.1).

Places API (New) returns `attributions[]` on a place: "A set of data provider that must be
shown with this result". They are kept in the record's stored payload, so they live and
are purged with it, and every screen that shows the business's Places data reads them from
here to show beside the Google Maps attribution.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.discovery.models import DiscoveredRecord
from app.modules.discovery.schemas import DataProviderRead


def data_providers(
    session: Session, business_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[DataProviderRead]]:
    """The third-party data providers each business's records say must be shown with them.

    Read from the stored payload's `attributions` (Places API (New)); a purged payload has
    none, and neither does the business's Places data by then. Each provider once.
    """
    if not business_ids:
        return {}
    attributions = DiscoveredRecord.raw_payload["attributions"]
    rows = session.execute(
        select(DiscoveredRecord.business_id, attributions)
        .where(DiscoveredRecord.business_id.in_(business_ids), attributions.is_not(None))
        .order_by(DiscoveredRecord.first_discovered_at.asc(), DiscoveredRecord.id.asc())
    ).all()
    grouped: dict[uuid.UUID, list[DataProviderRead]] = defaultdict(list)
    for business_id, items in rows:
        merge(grouped[business_id], parse(items))
    return dict(grouped)


def parse(items: object) -> list[DataProviderRead]:
    """A payload's `attributions` value, read defensively: a nameless entry is skipped."""
    found: list[DataProviderRead] = []
    for item in items if isinstance(items, list) else []:
        provider = item.get("provider") if isinstance(item, dict) else None
        if not provider:
            continue
        uri = item.get("providerUri")
        merge(
            found,
            [DataProviderRead(provider=str(provider), provider_uri=str(uri) if uri else None)],
        )
    return found


def merge(into: list[DataProviderRead], more: list[DataProviderRead]) -> list[DataProviderRead]:
    """Append each provider not already there, keeping first-seen order."""
    for entry in more:
        if entry not in into:
            into.append(entry)
    return into
