"""Compatibility shim for the observability-aware logging implementation."""

from app.obs.logging import *  # noqa: F401,F403

# Older imports and tests refer to this name. Keep it as an alias while the
# implementation lives under app/obs with the other observability pieces.
ColorFormatter = PrettyFormatter
