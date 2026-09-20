"""The health report (blueprint slide 45), one block per metric row in the spec's table."""

from datetime import datetime

from pydantic import BaseModel

from app.modules.alerts.schemas import AlertRead


class KindRate(BaseModel):
    kind: str
    done: int
    failed: int
    cancelled: int
    total: int
    # done / (done + failed); None when nothing finished in the window.
    success_rate: float | None


class JobSuccess(BaseModel):
    last_24h: list[KindRate]
    last_7d: list[KindRate]


class SourceErrorRate(BaseModel):
    source: str
    calls: int
    errors: int
    error_rate: float | None


class KindTiming(BaseModel):
    kind: str
    runs: int
    median_seconds: float | None
    p95_seconds: float | None


class QueueStatus(BaseModel):
    name: str
    length: int
    scheduler_lock_held: bool
    # Per scheduled job: its cron and when the scheduler last fired it.
    schedule: list["ScheduledJobStatus"]


class ScheduledJobStatus(BaseModel):
    name: str
    cron: str
    description: str
    last_fired_at: datetime | None
    last_run_status: str | None
    last_run_finished_at: datetime | None


class AIStatus(BaseModel):
    provider: str
    calls_today: int
    call_cap: int
    classifications_today: int
    reused_today: int
    reuse_rate: float | None
    spent_today_usd: float
    budget_usd: float
    # spent / budget with prices, calls / cap without. None when nothing to compare.
    budget_ratio: float | None
    prices_configured: bool


class DataQuality(BaseModel):
    records_total: int
    records_invalid: int
    invalid_rate: float | None
    businesses_total: int
    missing_city: int
    missing_phone: int
    missing_website: int


class DuplicateRate(BaseModel):
    """Last 7 days: what resolution merged on its own, what it sent to a human, and what
    humans decided."""

    auto_merged: int
    sent_to_review: int
    merged_by_review: int
    kept_apart: int
    pending_review: int


class CrmCounts(BaseModel):
    destination: str
    scheduled: int
    held: int
    synced_today: int


class SourceFreshness(BaseModel):
    source: str
    enabled: bool
    records: int
    last_discovered_at: datetime | None


class AuditOutcomes(BaseModel):
    done: int
    robots_blocked: int
    unreachable: int
    failed: int
    skipped: int
    total: int


class BackupStatus(BaseModel):
    directory: str
    backups_kept: int
    keep: int
    last_backup_file: str | None
    last_backup_at: datetime | None
    last_backup_size_bytes: int | None
    last_verify_at: datetime | None
    last_verify_ok: bool | None
    last_verify_file: str | None
    last_verify_error: str | None


class Thresholds(BaseModel):
    job_success_rate_min: float
    source_error_rate_max: float
    ai_budget_ratio: float
    backup_max_age_hours: int
    queue_length_max: int
    watchdog_stale_minutes: int


class HealthReport(BaseModel):
    generated_at: datetime
    environment: str
    db: bool
    redis: bool
    jobs: JobSuccess
    sources: list[SourceErrorRate]
    timings: list[KindTiming]
    queue: QueueStatus
    ai: AIStatus
    data_quality: DataQuality
    duplicates: DuplicateRate
    crm: CrmCounts
    freshness: list[SourceFreshness]
    audits: AuditOutcomes
    backups: BackupStatus
    thresholds: Thresholds
    # Open, unacknowledged alerts: what the banner shows.
    alerts: list[AlertRead]
