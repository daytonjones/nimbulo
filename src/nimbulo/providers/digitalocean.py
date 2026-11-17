from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
import yaml

from .. import secrets
from ..models import AuthState, Identity, LoginResult, ProviderStatus
from .base import Provider

DOCTL_CONFIG = Path(os.path.expanduser("~/.config/doctl/config.yaml"))
API_BASE = "https://api.digitalocean.com/v2"


class DigitalOceanProvider(Provider):
    key = "digitalocean"
    label = "Digital Ocean"
    cli_tool = None

    # --- token discovery -----------------------------------------------------

    def _token_from_env(self) -> str | None:
        return (
            os.environ.get("DIGITALOCEAN_ACCESS_TOKEN")
            or os.environ.get("DIGITALOCEAN_TOKEN")
            or os.environ.get("DO_API_TOKEN")
        )

    def _token_from_doctl(self) -> str | None:
        if not DOCTL_CONFIG.exists():
            return None
        try:
            data = yaml.safe_load(DOCTL_CONFIG.read_text()) or {}
        except yaml.YAMLError:
            return None
        # doctl stores per-context tokens under auth-contexts; the active one under access-token
        if "access-token" in data:
            return data["access-token"]
        contexts = data.get("auth-contexts") or {}
        active = data.get("context") or "default"
        return contexts.get(active)

    def _resolve_token(self) -> tuple[str | None, str]:
        token = self._token_from_env()
        if token:
            return token, "env:DIGITALOCEAN_ACCESS_TOKEN"
        token = self._token_from_doctl()
        if token:
            return token, f"file:{DOCTL_CONFIG}"
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
                message="no DO token found (env, ~/.config/doctl/config.yaml, or keyring)",
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
                message=f"token from {source} is not valid",
            )
        if r.status_code >= 400:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.ERROR,
                message=f"HTTP {r.status_code}: {r.text[:200]}",
            )
        data = r.json().get("account", {})
        ident = Identity(
            account=data.get("team", {}).get("name") if isinstance(data.get("team"), dict) else data.get("uuid"),
            user=data.get("email"),
            extra={"status": data.get("status"), "source": source},
        )
        return ProviderStatus(
            provider=self.key,
            state=AuthState.AUTHENTICATED,
            identity=ident,
            active_profile=source,
        )

    # --- login ---------------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["token"]

    def login(self, method: str = "token", **kwargs: Any) -> LoginResult:
        token = kwargs.get("token")
        source = "input"
        if not token:
            token, source = self._resolve_token()
            if not token:
                return LoginResult(
                    ok=False,
                    message="no token provided and none found in env, ~/.config/doctl/config.yaml, or keyring",
                )
        try:
            r = requests.get(
                f"{API_BASE}/account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            return LoginResult(ok=False, message=f"network error validating token: {exc}")
        if r.status_code == 401:
            return LoginResult(ok=False, message=f"token from {source} rejected by Digital Ocean")
        if r.status_code >= 400:
            return LoginResult(ok=False, message=f"unexpected response: HTTP {r.status_code}")
        if source != "keyring":
            secrets.set(self.key, token)
            return LoginResult(ok=True, message=f"token validated ({source}) and stored in keyring")
        return LoginResult(ok=True, message="token validated (already in keyring)")

    def logout(self, profile: str | None = None) -> LoginResult:
        # API tokens are not sessions; "logging out" is a no-op. Use forget() to
        # actually delete the stored token.
        if secrets.get(self.key):
            return LoginResult(
                ok=True,
                message="DO uses an API token (no session to log out of). The stored token was kept; use Forget Key to delete it.",
            )
        return LoginResult(
            ok=True,
            message="DO uses an API token (no session to log out of). No stored token to clear.",
        )

    def forget(self) -> LoginResult:
        if secrets.get(self.key) is None:
            return LoginResult(ok=True, message="no DO token stored in keyring")
        secrets.delete(self.key)
        return LoginResult(ok=True, message="deleted DO token from keyring (env/file untouched)")
