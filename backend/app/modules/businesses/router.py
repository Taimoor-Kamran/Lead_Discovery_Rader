"""`/businesses/*`: what resolution produced, and where every value came from."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import CurrentUser, DbSession
from app.modules.businesses import service
from app.modules.businesses.schemas import BusinessDetail, BusinessSummary
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind

businesses_router = APIRouter(prefix="/businesses", tags=["businesses"])


@businesses_router.get("", response_model=Page[BusinessSummary])
def list_businesses(
    user: CurrentUser,
    session: DbSession,
    industry: Annotated[str | None, Query()] = None,
    city: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    has_website: Annotated[bool | None, Query()] = None,
    website_kind: Annotated[WebsiteKind | None, Query()] = None,
    business_status: Annotated[BusinessStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[BusinessSummary]:
    return service.list_businesses(
        session,
        industry=industry,
        city=city,
        state=state,
        has_website=has_website,
        website_kind=website_kind,
        business_status=business_status,
        q=q,
        limit=limit,
        cursor=cursor,
    )


@businesses_router.get("/{business_id}", response_model=BusinessDetail)
def get_business(business_id: uuid.UUID, user: CurrentUser, session: DbSession) -> BusinessDetail:
    """The business plus every value recorded for it, displayed or not."""
    return service.detail(session, service.get_business(session, business_id))
