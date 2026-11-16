from __future__ import annotations

from .. import settings
from .aws import AWSProvider
from .azure import AzureProvider
from .base import Provider
from .digitalocean import DigitalOceanProvider
from .gcp import GCPProvider
from .oci import OCIProvider
from .vultr import VultrProvider


def _all() -> list[Provider]:
    return [
        AWSProvider(),
        GCPProvider(),
        AzureProvider(),
        OCIProvider(),
        VultrProvider(),
        DigitalOceanProvider(),
    ]


def _disabled_raw() -> set[str]:
    raw = settings.get("providers.disabled")
    if not isinstance(raw, list):
        return set()
    return {k for k in raw if isinstance(k, str)}


def disabled_keys() -> list[str]:
    """Valid disabled provider keys, in canonical order."""
    raw = _disabled_raw()
    return [p.key for p in _all() if p.key in raw]


def is_enabled(key: str) -> bool:
    return key.lower() not in disabled_keys()


def all_providers(include_disabled: bool = False) -> list[Provider]:
    """Providers in canonical order; enabled only unless include_disabled.

    If every provider is disabled (hand-edited settings), all are returned.
    """
    everything = _all()
    if include_disabled:
        return everything
    off = _disabled_raw()
    enabled = [p for p in everything if p.key not in off]
    return enabled or everything


def set_enabled(key: str, enabled: bool) -> tuple[bool, str]:
    """Enable or disable a provider. Returns (ok, message); no write on failure."""
    key = key.lower()
    keys = [p.key for p in _all()]
    if key not in keys:
        return False, f"unknown provider: {key}"
    off = set(disabled_keys())
    if enabled:
        off.discard(key)
    else:
        if key in off:
            return True, f"{key} is already disabled"
        if len(off | {key}) >= len(keys):
            return False, "cannot disable the last enabled provider"
        off.add(key)
    new = [k for k in keys if k in off]
    if new != disabled_keys():
        settings.set("providers.disabled", new)
    return True, f"{key} {'enabled' if enabled else 'disabled'}"


def get(name: str) -> Provider:
    name = name.lower()
    for p in _all():
        if p.key == name:
            return p
    raise KeyError(f"unknown provider: {name}")


__all__ = [
    "Provider",
    "AWSProvider",
    "GCPProvider",
    "AzureProvider",
    "OCIProvider",
    "VultrProvider",
    "DigitalOceanProvider",
    "all_providers",
    "disabled_keys",
    "is_enabled",
    "set_enabled",
    "get",
]
