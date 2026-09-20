"""User model and the role enum from blueprint slide 7."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column, uuid_pk


class Role(enum.StrEnum):
    admin = "admin"
    sales_rep = "sales_rep"
    reviewer = "reviewer"
    tech_admin = "tech_admin"
    crm_manager = "crm_manager"


role_enum = SAEnum(Role, name="user_role", values_callable=lambda e: [m.value for m in e])


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(role_enum, nullable=False, default=Role.sales_rep)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Bumped whenever the password is reset. Every token carries the version it was
    # issued under, so raising it invalidates every token already out there at once.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # v0.8.0: a user created or reset by an admin may only change their password until
    # they have done so; failed logins count towards a temporary lock.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} role={self.role.value}>"
