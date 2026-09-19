"""The adapter registry, and the bridge from adapters to `sources` rows.

Adapters are registered by name at import time; `sources.name` is the same string, which
is how a `search_jobs.source_ids` entry finds the code that can run it.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.adapters.base import SourceAdapter
from app.modules.adapters.errors import AdapterError
from app.modules.sources.models import Source

logger = get_logger("app.adapters")

_ADAPTERS: dict[str, SourceAdapter] = {}
_bootstrapped = False


def _ensure_builtins() -> None:
    """Import the built-in adapters the first time the registry is used.

    Deferred so that no entrypoint has to remember to do it, and so the import happens
    after `app.core.http` is fully loaded.
    """
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    from app.modules.adapters.builtin import register_builtin_adapters

    register_builtin_adapters()


class UnknownAdapterError(AdapterError):
    status_code = 404
    code = "unknown_source"


def register(adapter: SourceAdapter, *, replace: bool = False) -> SourceAdapter:
    """Add an adapter. A duplicate name is a programming error unless `replace` is set."""
    _ensure_builtins()
    name = adapter.name
    if not name:
        raise ValueError("An adapter must have a non-empty name")
    if name in _ADAPTERS and not replace:
        raise ValueError(f"An adapter named '{name}' is already registered")
    _ADAPTERS[name] = adapter
    return adapter


def reload_builtins() -> None:
    """Re-run built-in registration. Used after the environment changes (tests, the CLI)."""
    global _bootstrapped
    _bootstrapped = False
    _ensure_builtins()


def unregister(name: str) -> SourceAdapter | None:
    """Remove an adapter. Used by tests to install a temporary one."""
    return _ADAPTERS.pop(name, None)


def get(name: str) -> SourceAdapter:
    _ensure_builtins()
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise UnknownAdapterError(
            f"No adapter is registered for source '{name}'", details={"source": name}
        ) from exc


def all() -> list[SourceAdapter]:  # `all` is the name the adapter contract uses
    """Every registered adapter, in name order."""
    _ensure_builtins()
    return [_ADAPTERS[name] for name in sorted(_ADAPTERS)]


def names(*, bootstrap: bool = True) -> list[str]:
    if bootstrap:
        _ensure_builtins()
    return sorted(_ADAPTERS)


def sync_sources(session: Session) -> list[Source]:
    """Upsert one `sources` row per registered adapter. Idempotent.

    An operator's `enabled` choice is never overwritten; only the metadata is refreshed.
    """
    synced: list[Source] = []
    existing = {row.name: row for row in session.scalars(select(Source))}
    for adapter in all():
        config: dict[str, Any] = adapter.get_source_metadata().as_config(adapter.get_rate_limit())
        source = existing.get(adapter.name)
        if source is None:
            source = Source(name=adapter.name, kind=adapter.kind, config=config, enabled=True)
            session.add(source)
            logger.info("source registered", extra={"source": adapter.name})
        else:
            source.kind = adapter.kind
            source.config = config
        synced.append(source)
    session.flush()
    return synced
