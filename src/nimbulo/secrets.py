"""Thin wrapper around the system keyring for Vultr/DO API tokens."""
from __future__ import annotations

import keyring
from keyring.errors import KeyringError

SERVICE = "nimbulo"
# Tokens stored before the project was renamed live under the old service name. They are
# found (and copied to the new name) on first read; the old entry is only removed by delete().
LEGACY_SERVICE = "cloud-login"


def get(provider: str) -> str | None:
    try:
        token = keyring.get_password(SERVICE, provider)
        if token:
            return token
        token = keyring.get_password(LEGACY_SERVICE, provider)
        if token:
            try:
                keyring.set_password(SERVICE, provider, token)
            except KeyringError:
                pass
        return token
    except KeyringError:
        return None


def set(provider: str, token: str) -> None:
    keyring.set_password(SERVICE, provider, token)


def delete(provider: str) -> None:
    for service in (SERVICE, LEGACY_SERVICE):
        try:
            keyring.delete_password(service, provider)
        except KeyringError:
            pass
