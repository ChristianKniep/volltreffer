"""Betting-backend plugin layer.

A *betting provider* is an online tipping site (teamtip.net, Kicktipp, …) the
app can read tips from and push tips to. Each provider is a subclass of
``BetProvider`` registered with ``@register``. The rest of the app never talks
to a site directly — it asks the registry for a provider and hands it the
current user's (decrypted) credentials.

Adding a new site = drop a new module in this package that subclasses
``BetProvider`` and decorates it with ``@register``; importing the package
auto-discovers it. No other file needs to change.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CredField:
    """One credential the provider needs, rendered as an input in Settings."""
    name: str                      # key stored in the encrypted blob
    label: str                     # shown to the user
    type: str = "text"             # text | password | number
    required: bool = True
    help: str = ""
    placeholder: str = ""
    secret: bool = False           # never echoed back to the client once saved

    def asdict(self) -> dict:
        return {
            "name": self.name, "label": self.label, "type": self.type,
            "required": self.required, "help": self.help,
            "placeholder": self.placeholder, "secret": self.secret,
        }


class BetProvider:
    """Base class for a betting backend. Subclass + ``@register``.

    Subclasses set ``id``/``label``/``credential_fields`` and implement
    ``sync_tips`` and ``submit_tip``. Credentials arrive as a plain dict
    (already decrypted) keyed by ``CredField.name``.
    """
    id: str = ""
    label: str = ""
    blurb: str = ""
    credential_fields: list[CredField] = []

    # ---- capability hooks (override as needed) ----
    def validate(self, creds: dict) -> tuple[bool, str]:
        """Cheap credential check (e.g. hit a read endpoint). Best-effort."""
        return True, ""

    def sync_tips(self, conn, user_id: int, creds: dict) -> dict:
        """Pull the user's tips from the site into the local ``tips`` table."""
        raise NotImplementedError

    def submit_tip(self, conn, user_id: int, creds: dict,
                   match_id: str, home: int, away: int) -> dict:
        """Push one tip to the site and mirror it into ``tips``."""
        raise NotImplementedError

    # ---- introspection used by the API/UI ----
    @classmethod
    def describe(cls) -> dict:
        return {
            "id": cls.id, "label": cls.label, "blurb": cls.blurb,
            "fields": [f.asdict() for f in cls.credential_fields],
        }


_REGISTRY: dict[str, type[BetProvider]] = {}


def register(cls: type[BetProvider]) -> type[BetProvider]:
    if not cls.id:
        raise ValueError(f"{cls.__name__} must set a non-empty id")
    _REGISTRY[cls.id] = cls
    return cls


def _autodiscover() -> None:
    """Import every submodule so their ``@register`` decorators run."""
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{mod.name}")


def all_providers() -> list[type[BetProvider]]:
    if not _REGISTRY:
        _autodiscover()
    return list(_REGISTRY.values())


def get_provider(pid: str) -> BetProvider | None:
    if not _REGISTRY:
        _autodiscover()
    cls = _REGISTRY.get(pid)
    return cls() if cls else None
