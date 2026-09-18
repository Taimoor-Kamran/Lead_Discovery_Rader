"""`businesses` and `business_field_values` (blueprint slides 25-27 and §3 Provenance).

A business row is a *view*: every displayed value is derived from the field values below
it, so nothing on it is a fact in its own right. That is what makes both survivorship and
retention work — expiring a source's values recomputes the business rather than editing it.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column, uuid_pk
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind

website_kind_enum = SAEnum(
    WebsiteKind, name="website_kind", values_callable=lambda e: [m.value for m in e]
)
business_status_enum = SAEnum(
    BusinessStatus, name="business_status", values_callable=lambda e: [m.value for m in e]
)

# Every field survivorship maintains, in the order a detail view reads best.
PROVENANCED_FIELDS = (
    "display_name",
    "normalized_name",
    "name_key",
    "legal_name",
    "industry",
    "address_line1",
    "address_line2",
    "street_key",
    "city",
    "state",
    "postal_code",
    "country",
    "lat",
    "lng",
    "geohash7",
    "phone_e164",
    "website",
    "domain",
    "website_kind",
    "business_status",
)


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        # Deliberately not unique: two locations of one chain share a domain, and the
        # resolver sends that pair to a human instead of merging it.
        Index("ix_businesses_domain", "domain"),
        Index("ix_businesses_phone_e164", "phone_e164"),
        Index("ix_businesses_postal_code_name_key", "postal_code", "name_key"),
        Index("ix_businesses_geohash7", "geohash7"),
        Index("ix_businesses_industry", "industry"),
        Index("ix_businesses_state_city", "state", "city"),
        Index("ix_businesses_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)

    address_line1: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_line2: Mapped[str | None] = mapped_column(Text, nullable=True)
    street_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    geohash7: Mapped[str | None] = mapped_column(Text, nullable=True)

    phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_kind: Mapped[WebsiteKind] = mapped_column(
        website_kind_enum, nullable=False, default=WebsiteKind.none
    )
    business_status: Mapped[BusinessStatus] = mapped_column(
        business_status_enum, nullable=False, default=BusinessStatus.unknown
    )
    # The earliest expiry among the provider content this business is built from; it is
    # why a name may read `[expired] <place_id>` after `purge-expired` has run.
    places_content_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class BusinessFieldValue(Base):
    """One source's answer for one field of one business, with when and where it came from."""

    __tablename__ = "business_field_values"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "field",
            "discovered_record_id",
            name="uq_business_field_values_business_field_record",
        ),
        Index("ix_business_field_values_business_id_field", "business_id", "field"),
        Index("ix_business_field_values_expires_at", "expires_at"),
        Index("ix_business_field_values_discovered_record_id", "discovered_record_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    discovered_record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discovered_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set by `purge-expired`; the row survives so the provenance trail is never broken.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
