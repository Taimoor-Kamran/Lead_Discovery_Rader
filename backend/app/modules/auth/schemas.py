"""Auth and user request/response models."""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from app.modules.auth.models import Role

MIN_PASSWORD_LENGTH = 12


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - a scheme name, not a credential
    expires_at: datetime


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    is_active: bool
    # v0.8.0: the forced-change flag, the lock, and when they last signed in.
    must_change_password: bool = False
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > datetime.now(UTC)


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    role: Role
    is_active: bool = True
    # A user an admin creates gets a temporary password and must replace it first thing.
    must_change_password: bool = True


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=256)


class PasswordResetRequest(BaseModel):
    """An admin sets a new temporary password; the user must change it on next sign-in."""

    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
