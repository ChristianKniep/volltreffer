"""Deprecated shim.

The teamtip integration moved into the pluggable betting-backend layer at
``app/providers/teamtip.py``. Credentials are now per-user (entered in the
Settings pane) instead of global env vars. This module is kept only so any
external import of ``app.teamtip`` still resolves; it re-exports the provider.
"""
from .providers.teamtip import TeamtipProvider, DE2EN  # noqa: F401

provider = TeamtipProvider()
