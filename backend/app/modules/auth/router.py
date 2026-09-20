"""`/auth/*` and `/users/*` endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.config import get_settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.modules.auth import service
from app.modules.auth.deps import CurrentUser, DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetRequest,
    TokenResponse,
    UserCreate,
    UserRead,
    UserUpdate,
)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])

AdminUser = Annotated[User, Depends(require_role(Role.admin))]


def _set_refresh_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    token, _ = create_refresh_token(user.id, user.role.value, user.token_version)
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        secure=settings.resolved_refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=settings.api_v1_prefix + "/auth",
    )


def _client_ip(request: Request) -> str:
    """The connecting address. No proxy sits in front of this spec's deployment, so a
    forwarded header is not trusted; a reverse proxy is the v1.1 spec's business."""
    return request.client.host if request.client else service.UNKNOWN_CLIENT


@auth_router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, session: DbSession
) -> TokenResponse:
    user = service.authenticate(
        session, str(payload.email), payload.password, client_ip=_client_ip(request)
    )
    access_token, expires_at = create_access_token(user.id, user.role.value, user.token_version)
    _set_refresh_cookie(response, user)
    return TokenResponse(access_token=access_token, expires_at=expires_at)


@auth_router.post("/change-password", response_model=TokenResponse)
def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, response: Response, session: DbSession
) -> TokenResponse:
    """Replace your own password. Every other session is signed out; this one continues."""
    changed = service.change_password(
        session,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    access_token, expires_at = create_access_token(
        changed.id, changed.role.value, changed.token_version
    )
    _set_refresh_cookie(response, changed)
    return TokenResponse(access_token=access_token, expires_at=expires_at)


def _refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(get_settings().refresh_cookie_name)


@auth_router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, session: DbSession) -> TokenResponse:
    token = _refresh_cookie(request)
    if not token:
        raise AuthenticationError("Refresh token is missing", code="unauthenticated")

    claims = decode_token(token, "refresh")
    user = session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise AuthenticationError("Refresh token is no longer valid", code="unauthenticated")
    # A password reset retires every refresh token issued before it.
    if claims.token_version != user.token_version:
        raise AuthenticationError("Refresh token has been revoked", code="token_revoked")

    access_token, expires_at = create_access_token(user.id, user.role.value, user.token_version)
    _set_refresh_cookie(response, user)
    service.record_refresh(session, user)
    return TokenResponse(access_token=access_token, expires_at=expires_at)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, session: DbSession) -> Response:
    """Clears the refresh cookie. Always succeeds, so a stale session can always log out."""
    settings = get_settings()
    user_id: uuid.UUID | None = None
    token = _refresh_cookie(request)
    if token:
        try:
            user_id = decode_token(token, "refresh").subject
        except AuthenticationError:
            user_id = None

    service.record_logout(session, user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.api_v1_prefix + "/auth",
        httponly=True,
        secure=settings.resolved_refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )
    return response


@auth_router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@users_router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, actor: AdminUser, session: DbSession) -> UserRead:
    user = service.create_user(session, payload, actor_id=actor.id)
    return UserRead.model_validate(user)


@users_router.get("", response_model=Page[UserRead])
def list_users(
    actor: CurrentUser,
    session: DbSession,
    role: Annotated[Role | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[UserRead]:
    """Admins list anyone. A reviewer may list active sales reps, for the assignment picker."""
    if actor.role is not Role.admin:
        if actor.role is not Role.reviewer or role is not Role.sales_rep:
            raise PermissionDeniedError(
                "Your role may only list active sales reps (role=sales_rep)",
                details={"required": ["admin", "reviewer"]},
            )
        is_active = True
    return service.list_users(
        session,
        role=role,
        is_active=is_active,
        limit=limit,
        cursor=cursor,
        with_login_state=actor.role is Role.admin,
    )


@users_router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: uuid.UUID, payload: UserUpdate, actor: AdminUser, session: DbSession
) -> UserRead:
    user = service.update_user(session, user_id, payload, actor_id=actor.id)
    return UserRead.model_validate(user)


@users_router.post("/{user_id}/unlock", response_model=UserRead)
def unlock_user(user_id: uuid.UUID, actor: AdminUser, session: DbSession) -> UserRead:
    """Lift a lockout before it expires on its own. Clears the account lock and every
    rate-limit counter for the email, from any address."""
    return UserRead.model_validate(service.unlock_user(session, user_id, actor_id=actor.id))


@users_router.post("/{user_id}/reset-password", response_model=UserRead)
def reset_user_password(
    user_id: uuid.UUID, payload: PasswordResetRequest, actor: AdminUser, session: DbSession
) -> UserRead:
    """Set a temporary password. The user must change it on their next sign-in."""
    user = service.admin_reset_password(session, user_id, payload.password, actor_id=actor.id)
    return UserRead.model_validate(user)
