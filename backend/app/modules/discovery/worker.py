"""The `discovery` job handler: run every source of a search job and store what it finds.

One pass per enabled source. Each result is validated before it is stored; an invalid
record is counted and dropped rather than guessed at. Cancellation is checked between
records, so a cancelled run keeps everything it had already stored.
"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_fields
from app.modules.adapters import registry
from app.modules.adapters.base import DiscoveryConfig, SourceAdapter
from app.modules.audit import service as audit
from app.modules.discovery import service
from app.modules.discovery.schemas import DiscoveryResultSummary
from app.modules.jobs.models import JobRun, SearchJob
from app.modules.jobs.schemas import GeoSpec
from app.modules.jobs.service import effective_max_results, get_search_job
from app.modules.sources.models import Source

logger = get_logger("app.discovery.worker")


def run_discovery(session: Session, run: JobRun) -> None:
    """The handler registered for job kind `discovery`.

    `checkpoint` is imported lazily because `app.workers.tasks` imports this module.
    """
    from app.workers.tasks import checkpoint

    if run.search_job_id is None:
        raise ValueError("A discovery run must belong to a search job")
    job = get_search_job(session, run.search_job_id)

    audit.record(
        session,
        action="discovery.run_started",
        entity_type="job_run",
        entity_id=run.id,
        after={"search_job_id": str(job.id), "source_ids": [str(s) for s in job.source_ids]},
    )

    summary = DiscoveryResultSummary()
    run.progress_total = 0
    run.progress_done = 0
    checkpoint(session, run, done=0)

    for source in _sources_for(session, job):
        adapter = registry.get(source.name)
        _run_one_source(session, run, job, source, adapter, summary)

    summary.api_calls = service.count_api_calls(session, run.id)
    run.result_summary = summary.model_dump()
    audit.record(
        session,
        action="discovery.run_finished",
        entity_type="job_run",
        entity_id=run.id,
        after=summary.model_dump(),
    )
    session.flush()
    logger.info(
        "discovery finished",
        extra=log_fields(job_run_id=str(run.id), search_job_id=str(job.id), **summary.model_dump()),
    )


def _run_one_source(
    session: Session,
    run: JobRun,
    job: SearchJob,
    source: Source,
    adapter: SourceAdapter,
    summary: DiscoveryResultSummary,
) -> None:
    from app.workers.tasks import checkpoint

    ttl_days = int(adapter.get_source_metadata().content_ttl_days)
    cfg = DiscoveryConfig(
        industry=job.industry,
        geo=GeoSpec.model_validate(job.geo),
        job_run_id=run.id,
        max_results=effective_max_results(job),
        cancel_check=lambda: checkpoint(session, run),
    )

    for rank, ref in enumerate(adapter.discover(cfg)):
        checkpoint(session, run)
        summary.fetched += 1
        run.progress_total = summary.fetched

        raw = adapter.fetch(ref)
        result = adapter.validate(raw)
        if not result.valid:
            summary.invalid += 1
            logger.warning(
                "discarded an invalid record",
                extra={
                    "source": source.name,
                    "source_record_id": raw.source_record_id,
                    "errors": result.errors,
                },
            )
            checkpoint(session, run, done=summary.stored_new + summary.updated)
            continue

        record, created = service.store_raw(
            session, source_id=source.id, raw=raw, content_ttl_days=ttl_days
        )
        service.add_sighting(
            session,
            record=record,
            job_run_id=run.id,
            search_job_id=job.id,
            rank=rank,
        )
        if created:
            summary.stored_new += 1
        else:
            summary.updated += 1
        checkpoint(session, run, done=summary.stored_new + summary.updated)


def _sources_for(session: Session, job: SearchJob) -> list[Source]:
    """The job's enabled sources. A disabled one is skipped with a warning, not an error."""
    usable: list[Source] = []
    for source_id in job.source_ids:
        source = session.get(Source, source_id)
        if source is None:
            logger.warning(
                "search job names a source that no longer exists",
                extra={"search_job_id": str(job.id), "source_id": str(source_id)},
            )
            continue
        if not source.enabled:
            logger.warning(
                "skipping a disabled source",
                extra={"search_job_id": str(job.id), "source": source.name},
            )
            continue
        usable.append(source)
    return usable
