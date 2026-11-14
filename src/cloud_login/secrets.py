"""Thin wrapper around the system keyring for Vultr/DO API tokens."""
from __future__ import annotations

import keyring
from keyring.errors import KeyringError

SERVICE = "cloud-login"


def get(provider: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, provider)
    except KeyringError:
        return None


def set(provider: str, token: str) -> None:
    keyring.set_password(SERVICE, provider, token)


def delete(provider: str) -> None:
    try:
        keyring.delete_password(SERVICE, provider)
    except KeyringError:
        pass
