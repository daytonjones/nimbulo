from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..models import AuthState, Identity, LoginResult, ProviderStatus
from .base import OutputCb, Provider

GCLOUD_DIR = Path(os.path.expanduser("~/.config/gcloud"))
ADC_FILE = GCLOUD_DIR / "application_default_credentials.json"


class GCPProvider(Provider):
    key = "gcp"
    label = "Google Cloud"
    cli_tool = "gcloud"
    profile_label = "Configuration"
    secondary_label = "Project"

    # --- discovery -----------------------------------------------------------

    def _configurations(self) -> list[str]:
        rc, out, _ = self._run(
            ["gcloud", "config", "configurations", "list", "--format=json"],
            timeout=15,
        )
        if rc != 0:
            return []
        try:
            return [c["name"] for c in json.loads(out)]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def _active_account_and_project(self) -> tuple[str | None, str | None]:
        rc, out, _ = self._run(
            ["gcloud", "config", "configurations", "list", "--format=json"],
            timeout=15,
        )
        if rc != 0:
            return None, None
        try:
            confs = json.loads(out)
        except json.JSONDecodeError:
            return None, None
        for c in confs:
            if c.get("is_active"):
                props = c.get("properties", {}) or {}
                core = props.get("core", {}) or {}
                return core.get("account"), core.get("project")
        return None, None

    # --- status --------------------------------------------------------------

    def status(self, profile: str | None = None) -> ProviderStatus:
        profiles = self._configurations()
        if not self.cli_available():
            return ProviderStatus(
                provider=self.key,
                state=AuthState.NOT_INSTALLED,
                profiles=profiles,
                message="gcloud CLI not found on PATH",
            )
        # If a profile (configuration) was specified, activate it for this call only via env var
        env_override = None
        if profile:
            env_override = profile
        rc, out, err = self._run(
            ["gcloud", "auth", "list", "--format=json"]
            + (["--configuration", env_override] if env_override else []),
            timeout=15,
        )
        if rc != 0:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.ERROR,
                profiles=profiles,
                message=(err or out or "gcloud auth list failed").strip(),
            )
        try:
            accounts = json.loads(out)
        except json.JSONDecodeError:
            accounts = []
        active = next((a for a in accounts if a.get("status") == "ACTIVE"), None)
        account, project = self._active_account_and_project()
        if not active and not account:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.UNAUTHENTICATED,
                profiles=profiles,
                active_profile=profile,
                message="no active gcloud account",
            )
        adc_present = ADC_FILE.exists()
        ident = Identity(
            account=project,
            user=(active or {}).get("account") or account,
            extra={"adc_present": adc_present},
        )
        return ProviderStatus(
            provider=self.key,
            state=AuthState.AUTHENTICATED,
            identity=ident,
            active_profile=profile,
            profiles=profiles,
        )

    # --- login ---------------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["user+adc", "user", "adc"]

    def _user_login_cmd(self, kwargs: dict[str, Any]) -> list[str]:
        cmd = ["gcloud"]
        if kwargs.get("configuration"):
            cmd += ["--configuration", kwargs["configuration"]]
        cmd += ["auth", "login"]
        if kwargs.get("account"):
            cmd.append(kwargs["account"])
        return cmd

    def login(self, method: str = "user+adc", on_output: OutputCb = None, **kwargs: Any) -> LoginResult:
        if method in {"user", "user+adc"}:
            rc, out = self._popen_stream(self._user_login_cmd(kwargs), on_output=on_output, timeout=600)
            if rc != 0:
                return LoginResult(ok=False, message="gcloud auth login failed", output=out)
            if kwargs.get("project"):
                self._run(["gcloud", "config", "set", "project", kwargs["project"]], timeout=15)
            if method == "user":
                return LoginResult(ok=True, message="gnimbulo complete", output=out)
            if on_output:
                on_output("--- starting application-default (ADC) login ---")
            rc2, out2 = self._popen_stream(
                ["gcloud", "auth", "application-default", "login"],
                on_output=on_output,
                timeout=600,
            )
            full = out + "\n" + out2
            if rc2 != 0:
                return LoginResult(ok=False, message="user login OK, ADC login failed", output=full)
            return LoginResult(ok=True, message="user + ADC login complete", output=full)
        if method == "adc":
            rc, out = self._popen_stream(
                ["gcloud", "auth", "application-default", "login"],
                on_output=on_output,
                timeout=600,
            )
            return LoginResult(ok=(rc == 0), message="ADC login complete" if rc == 0 else "ADC login failed", output=out)
        return LoginResult(ok=False, message=f"unknown login method: {method}")

    # --- projects ------------------------------------------------------------

    def list_projects(self) -> list[tuple[str, str]]:
        """Return (project_id, display_name) tuples visible to the active account."""
        rc, out, _ = self._run(
            ["gcloud", "projects", "list", "--format=json"],
            timeout=30,
        )
        if rc != 0:
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        return [
            (p["projectId"], p.get("name") or p["projectId"])
            for p in data
            if isinstance(p, dict) and p.get("projectId")
        ]

    def set_project(self, project_id: str) -> LoginResult:
        rc, out, err = self._run(["gcloud", "config", "set", "project", project_id], timeout=15)
        if rc == 0:
            return LoginResult(ok=True, message=f"active project set to {project_id}")
        return LoginResult(ok=False, message=(err or out or "gcloud config set project failed").strip())

    # generic secondary hooks — delegate to project methods
    def list_secondary(self) -> list[tuple[str, str]]:
        return self.list_projects()

    def set_secondary(self, sid: str) -> LoginResult:
        return self.set_project(sid)

    def logout(self, profile: str | None = None) -> LoginResult:
        cmd = ["gcloud", "auth", "revoke", "--all"]
        rc, out, err = self._run(cmd, timeout=30)
        return LoginResult(ok=(rc == 0), message="revoked all gcloud credentials" if rc == 0 else (err or out), output=out + err)
