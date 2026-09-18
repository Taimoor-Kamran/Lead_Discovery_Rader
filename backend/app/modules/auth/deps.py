"""Authentication and role dependencies."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token
from app.modules.auth.models import Role, User

DbSession = Annotated[Session, Depends(get_db)]


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Authentication required", code="unauthenticated")
    return token.strip()


def get_current_user(request: Request, session: DbSession) -> User:
    claims = decode_token(_bearer_token(request), "access")
    user = session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise AuthenticationError("Authentication required", code="unauthenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[User], User]:
    """Dependency factory: allow only these roles. `admin` always passes."""
    allowed = frozenset(roles) | {Role.admin}

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError(
                "Your role may not perform this action",
                details={"required": sorted(r.value for r in allowed)},
            )
        return user

    return dependency
