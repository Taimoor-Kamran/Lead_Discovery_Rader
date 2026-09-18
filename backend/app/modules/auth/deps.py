"""Authentication and role dependencies."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token
from app.modules.auth.models import Role, User

DbSession = Annotated[Session, Depends(get_db)]

# `auto_error=False` so a missing or malformed header raises our own error rather than
# FastAPI's bare 403. Declaring the scheme is also what puts the **Authorize** button on
# /docs: it is how the bearer scheme reaches `openapi.json`.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearerAuth", description="Access token")
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _bearer_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials.strip()
        if token:
            return token
    # Fallback for a header FastAPI's parser rejected, so the message stays ours.
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Authentication required", code="unauthenticated")
    return token.strip()


def get_current_user(
    request: Request, session: DbSession, credentials: BearerCredentials = None
) -> User:
    claims = decode_token(_bearer_token(request, credentials), "access")
    user = session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise AuthenticationError("Authentication required", code="unauthenticated")
    # A password reset raises `token_version`, which retires every token issued before it.
    if claims.token_version != user.token_version:
        raise AuthenticationError("Token is no longer valid", code="token_revoked")
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
