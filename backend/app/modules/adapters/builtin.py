"""Registration of the adapters shipped with this build.

Kept out of `adapters/__init__.py` on purpose: `app.core.http` imports `adapters.errors`,
so anything heavy in the package's `__init__` would close an import loop.
"""

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter


def register_builtin_adapters() -> None:
    """Idempotent: safe to call from every entrypoint and from tests."""
    if GooglePlacesAdapter.name not in registry.names(bootstrap=False):
        registry.register(GooglePlacesAdapter())
