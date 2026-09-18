"""Imports every model module so `Base.metadata` is complete.

Alembic and the test harness both rely on this single import point.
"""

from app.core.db import Base
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.businesses.models import Business, BusinessFieldValue
from app.modules.discovery.models import ApiCall, DiscoveredRecord, RecordSighting
from app.modules.jobs.models import JobRun, SearchJob
from app.modules.resolution.models import MatchCandidate
from app.modules.sources.models import Source

__all__ = [
    "ApiCall",
    "AuditLog",
    "Base",
    "Business",
    "BusinessFieldValue",
    "DiscoveredRecord",
    "JobRun",
    "MatchCandidate",
    "RecordSighting",
    "SearchJob",
    "Source",
    "User",
]
