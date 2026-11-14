from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
import yaml

from .. import secrets
from ..models import AuthState, Identity, LoginResult, ProviderStatus
from .base import Provider

VULTR_CLI_CONFIG = Path(os.path.expanduser("~/.vultr-cli.yaml"))
API_BASE = "https://api.vultr.com/v2"


class VultrProvider(Provider):
    key = "vultr"
    label = "Vultr"
    cli_tool = None  # we hit the API directly

    # --- token discovery -----------------------------------------------------

    def _token_from_env(self) -> str | None:
        return os.environ.get("VULTR_API_KEY")

    def _token_from_cli_config(self) -> str | None:
        if not VULTR_CLI_CONFIG.exists():
            return None
        try:
            data = yaml.safe_load(VULTR_CLI_CONFIG.read_text()) or {}
        except yaml.YAMLError:
            return None
        return data.get("api-key") or data.get("api_key")

    def _resolve_token(self) -> tuple[str | None, str]:
        token = self._token_from_env()
        if token:
            return token, "env:VULTR_API_KEY"
        token = self._token_from_cli_config()
        if token:
            return token, f"file:{VULTR_CLI_CONFIG}"
        token = secrets.get(self.key)
        if token:
            return token, "keyring"
        return None, "missing"

    # --- status --------------------------------------------------------------

    def status(self, profile: str | None = None) -> ProviderStatus:
        token, source = self._resolve_token()
        if not token:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.UNAUTHENTICATED,
                message="no Vultr API key found (env, ~/.vultr-cli.yaml, or keyring)",
            )
        try:
            r = requests.get(
                f"{API_BASE}/account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.ERROR,
                message=f"network error: {exc}",
            )
        if r.status_code == 401:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.UNAUTHENTICATED,
                message=f"API key from {source} is not valid",
            )
        if r.status_code >= 400:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.ERROR,
                message=f"HTTP {r.status_code}: {r.text[:200]}",
            )
        data = r.json().get("account", {})
        ident = Identity(
            account=data.get("name") or data.get("email"),
            user=data.get("email"),
            extra={"balance": data.get("balance"), "source": source},
        )
        return ProviderStatus(
            provider=self.key,
            state=AuthState.AUTHENTICATED,
            identity=ident,
            active_profile=source,
        )

    # --- login ---------------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["api-key"]

    def login(self, method: str = "api-key", **kwargs: Any) -> LoginResult:
        token = kwargs.get("token") or kwargs.get("api_key")
        source = "input"
        if not token:
            token, source = self._resolve_token()
            if not token:
                return LoginResult(
                    ok=False,
                    message="no API key provided and none found in env, ~/.vultr-cli.yaml, or keyring",
                )
        try:
            r = requests.get(
                f"{API_BASE}/account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            return LoginResult(ok=False, message=f"network error validating key: {exc}")
        if r.status_code == 401:
            return LoginResult(ok=False, message=f"API key from {source} rejected by Vultr")
        if r.status_code >= 400:
            return LoginResult(ok=False, message=f"unexpected response: HTTP {r.status_code}")
        if source != "keyring":
            secrets.set(self.key, token)
            return LoginResult(ok=True, message=f"API key validated ({source}) and stored in keyring")
        return LoginResult(ok=True, message="API key validated (already in keyring)")

    def logout(self, profile: str | None = None) -> LoginResult:
        # API keys are not sessions; "logging out" is a no-op. Use forget() to
        # actually delete the stored key.
        if secrets.get(self.key):
            return LoginResult(
                ok=True,
                message="Vultr uses an API key (no session to log out of). The stored key was kept; use Forget Key to delete it.",
            )
        return LoginResult(
            ok=True,
            message="Vultr uses an API key (no session to log out of). No stored key to clear.",
        )

    def forget(self) -> LoginResult:
        if secrets.get(self.key) is None:
            return LoginResult(ok=True, message="no Vultr key stored in keyring")
        secrets.delete(self.key)
        return LoginResult(ok=True, message="deleted Vultr API key from keyring (env/file untouched)")
