"""Registration of the adapters shipped with this build.

Kept out of `adapters/__init__.py` on purpose: `app.core.http` imports `adapters.errors`,
so anything heavy in the package's `__init__` would close an import loop.
"""

from app.core.config import get_settings
from app.modules.adapters import registry
from app.modules.adapters.demo_fixture import DemoFixtureAdapter
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter


def register_builtin_adapters() -> None:
    """Idempotent: safe to call from every entrypoint and from tests."""
    registered = registry.names(bootstrap=False)
    if GooglePlacesAdapter.name not in registered:
        registry.register(GooglePlacesAdapter())

    # The demo fixture is a developer convenience, not a source. It exists only where
    # `APP_ENV` says so, which is what keeps fictional businesses out of staging and
    # production — `sync_sources` never creates a row for an adapter that is not here.
    if get_settings().is_development:
        if DemoFixtureAdapter.name not in registered:
            registry.register(DemoFixtureAdapter())
    else:
        registry.unregister(DemoFixtureAdapter.name)
