"""Source registry. Adapters arrive in v0.2.0; this spec only stores the rows."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, uuid_pk


class SourceKind(enum.StrEnum):
    api = "api"
    web = "web"
    feed = "feed"
    licensed = "licensed"


source_kind_enum = SAEnum(
    SourceKind, name="source_kind", values_callable=lambda e: [m.value for m in e]
)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    kind: Mapped[SourceKind] = mapped_column(source_kind_enum, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()
