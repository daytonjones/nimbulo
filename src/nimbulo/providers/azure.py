from __future__ import annotations

import json
from typing import Any

from ..models import AuthState, Identity, LoginResult, ProviderStatus
from .base import OutputCb, Provider


class AzureProvider(Provider):
    key = "azure"
    label = "Azure"
    cli_tool = "az"
    secondary_label = "Subscription"

    # --- status --------------------------------------------------------------

    def status(self, profile: str | None = None) -> ProviderStatus:
        if not self.cli_available():
            return ProviderStatus(
                provider=self.key,
                state=AuthState.NOT_INSTALLED,
                message="az CLI not found on PATH",
            )
        rc, out, err = self._run(["az", "account", "show", "--output", "json"], timeout=20)
        if rc != 0:
            msg = (err or out or "").strip().splitlines()[-1] if (err or out).strip() else "no active az session"
            state = AuthState.UNAUTHENTICATED
            if "expired" in msg.lower() or "refresh" in msg.lower():
                state = AuthState.EXPIRED
            return ProviderStatus(
                provider=self.key,
                state=state,
                message=msg,
            )
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.ERROR,
                message="could not parse `az account show` output",
            )
        user = data.get("user", {}) or {}
        ident = Identity(
            account=data.get("name"),  # subscription name
            user=user.get("name"),
            extra={
                "subscription_id": data.get("id"),
                "tenant_id": data.get("tenantId"),
                "state": data.get("state"),
            },
        )
        return ProviderStatus(
            provider=self.key,
            state=AuthState.AUTHENTICATED,
            identity=ident,
            active_profile=None,  # Azure has no "profile" concept
        )

    # --- login ---------------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["user", "device-code"]

    def login(self, method: str = "user", on_output: OutputCb = None, **kwargs: Any) -> LoginResult:
        if method == "user":
            cmd = ["az", "login"]
            if kwargs.get("tenant"):
                cmd += ["--tenant", kwargs["tenant"]]
            rc, out = self._popen_stream(cmd, on_output=on_output, timeout=600)
        elif method == "device-code":
            cmd = ["az", "login", "--use-device-code"]
            if kwargs.get("tenant"):
                cmd += ["--tenant", kwargs["tenant"]]
            rc, out = self._popen_stream(cmd, on_output=on_output, timeout=600)
        else:
            return LoginResult(ok=False, message=f"unknown login method: {method}")
        if rc != 0:
            return LoginResult(ok=False, message="az login failed", output=out)
        if kwargs.get("subscription"):
            self._run(["az", "account", "set", "--subscription", kwargs["subscription"]], timeout=15)
        return LoginResult(ok=True, message="az login complete", output=out)

    def logout(self, profile: str | None = None) -> LoginResult:
        rc, out, err = self._run(["az", "logout"], timeout=20)
        if rc == 0:
            return LoginResult(ok=True, message="cleared Azure session")
        return LoginResult(ok=False, message=(err or out or "az logout failed").strip())

    # --- subscriptions -------------------------------------------------------

    def list_subscriptions(self) -> list[tuple[str, str]]:
        rc, out, _ = self._run(
            ["az", "account", "list", "--output", "json", "--all"],
            timeout=30,
        )
        if rc != 0:
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        return [
            (s["id"], s.get("name") or s["id"])
            for s in data
            if isinstance(s, dict) and s.get("id")
        ]

    def set_subscription(self, subscription: str) -> LoginResult:
        rc, out, err = self._run(["az", "account", "set", "--subscription", subscription], timeout=15)
        if rc == 0:
            return LoginResult(ok=True, message=f"active subscription set to {subscription}")
        return LoginResult(ok=False, message=(err or out or "az account set failed").strip())

    # generic secondary hooks
    def list_secondary(self) -> list[tuple[str, str]]:
        return self.list_subscriptions()

    def set_secondary(self, sid: str) -> LoginResult:
        return self.set_subscription(sid)
