from __future__ import annotations

import configparser
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import AuthState, Identity, LoginResult, ProviderStatus
from .base import OutputCb, Provider

AWS_DIR = Path(os.path.expanduser("~/.aws"))
CONFIG_FILE = AWS_DIR / "config"
CREDENTIALS_FILE = AWS_DIR / "credentials"
SSO_CACHE_DIR = AWS_DIR / "sso" / "cache"


class AWSProvider(Provider):
    key = "aws"
    label = "AWS"
    cli_tool = "aws"
    profile_label = "Profile"

    # --- profile discovery ---------------------------------------------------

    def _read_config(self) -> configparser.ConfigParser:
        cp = configparser.ConfigParser()
        if CONFIG_FILE.exists():
            cp.read(CONFIG_FILE)
        return cp

    def _read_credentials(self) -> configparser.ConfigParser:
        cp = configparser.ConfigParser()
        if CREDENTIALS_FILE.exists():
            cp.read(CREDENTIALS_FILE)
        return cp

    def _profiles(self) -> list[str]:
        names: set[str] = set()
        for section in self._read_config().sections():
            # config sections are "[profile foo]" except "[default]"
            if section == "default":
                names.add("default")
            elif section.startswith("profile "):
                names.add(section[len("profile "):].strip())
        for section in self._read_credentials().sections():
            names.add(section)
        return sorted(names)

    def _profile_kind(self, profile: str) -> str:
        cfg = self._read_config()
        creds = self._read_credentials()
        section = "default" if profile == "default" else f"profile {profile}"
        opts: dict[str, str] = {}
        if cfg.has_section(section):
            opts.update(dict(cfg.items(section)))
        if creds.has_section(profile):
            opts.update(dict(creds.items(profile)))
        if "sso_start_url" in opts or "sso_session" in opts or "sso_account_id" in opts:
            return "sso"
        if "role_arn" in opts:
            return "role"
        if "aws_access_key_id" in opts:
            return "static"
        return "unknown"

    # --- status --------------------------------------------------------------

    def status(self, profile: str | None = None) -> ProviderStatus:
        profiles = self._profiles()
        if not self.cli_available():
            return ProviderStatus(
                provider=self.key,
                state=AuthState.NOT_INSTALLED,
                profiles=profiles,
                message="aws CLI not found on PATH",
            )
        target = profile or os.environ.get("AWS_PROFILE") or ("default" if "default" in profiles else (profiles[0] if profiles else None))
        if not target:
            return ProviderStatus(
                provider=self.key,
                state=AuthState.UNAUTHENTICATED,
                profiles=profiles,
                message="no AWS profiles configured",
            )
        rc, out, err = self._run(
            ["aws", "sts", "get-caller-identity", "--profile", target, "--output", "json"],
            timeout=20,
        )
        if rc == 0:
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                data = {}
            kind = self._profile_kind(target)
            ident = Identity(
                account=data.get("Account"),
                user=data.get("Arn"),
                expires_at=self._sso_expires_at(target) if kind == "sso" else None,
                extra={"UserId": data.get("UserId"), "kind": kind},
            )
            return ProviderStatus(
                provider=self.key,
                state=AuthState.AUTHENTICATED,
                identity=ident,
                active_profile=target,
                profiles=profiles,
            )
        msg = (err or out).strip().splitlines()[-1] if (err or out).strip() else "auth failed"
        # SSO session expired emits a particular error
        state = AuthState.EXPIRED if "sso" in msg.lower() and "expired" in msg.lower() else AuthState.UNAUTHENTICATED
        return ProviderStatus(
            provider=self.key,
            state=state,
            active_profile=target,
            profiles=profiles,
            message=msg,
        )

    # --- login ---------------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["sso", "access-key"]

    def login(self, method: str = "sso", on_output: OutputCb = None, **kwargs: Any) -> LoginResult:
        if method == "sso":
            profile = kwargs.get("profile")
            if not profile:
                return LoginResult(ok=False, message="profile is required for sso login")
            rc, output = self._popen_stream(
                ["aws", "sso", "login", "--profile", profile],
                on_output=on_output,
                timeout=300,
            )
            return LoginResult(
                ok=(rc == 0),
                message="sso login complete" if rc == 0 else "sso login failed",
                output=output,
            )
        if method == "access-key":
            profile = kwargs.get("profile") or "default"
            access_key = kwargs.get("access_key_id")
            secret = kwargs.get("secret_access_key")
            region = kwargs.get("region")
            session_token = kwargs.get("session_token")
            if not access_key or not secret:
                return LoginResult(ok=False, message="access_key_id and secret_access_key are required")
            AWS_DIR.mkdir(mode=0o700, exist_ok=True)
            creds = self._read_credentials()
            if not creds.has_section(profile):
                creds.add_section(profile)
            creds.set(profile, "aws_access_key_id", access_key)
            creds.set(profile, "aws_secret_access_key", secret)
            if session_token:
                creds.set(profile, "aws_session_token", session_token)
            with CREDENTIALS_FILE.open("w") as f:
                creds.write(f)
            os.chmod(CREDENTIALS_FILE, 0o600)
            if region:
                cfg = self._read_config()
                section = "default" if profile == "default" else f"profile {profile}"
                if not cfg.has_section(section):
                    cfg.add_section(section)
                cfg.set(section, "region", region)
                with CONFIG_FILE.open("w") as f:
                    cfg.write(f)
                os.chmod(CONFIG_FILE, 0o600)
            return LoginResult(ok=True, message=f"wrote access key to ~/.aws/credentials [{profile}]")
        return LoginResult(ok=False, message=f"unknown login method: {method}")

    # --- sso cache expiry ---------------------------------------------------

    def _profile_start_url(self, profile: str) -> str | None:
        cfg = self._read_config()
        section = "default" if profile == "default" else f"profile {profile}"
        if not cfg.has_section(section):
            return None
        opts = dict(cfg.items(section))
        if opts.get("sso_start_url"):
            return opts["sso_start_url"]
        # Named sso-session indirection
        session = opts.get("sso_session")
        if session and cfg.has_section(f"sso-session {session}"):
            return dict(cfg.items(f"sso-session {session}")).get("sso_start_url")
        return None

    def _sso_expires_at(self, profile: str) -> datetime | None:
        start_url = self._profile_start_url(profile)
        if not start_url or not SSO_CACHE_DIR.exists():
            return None
        latest: datetime | None = None
        for f in SSO_CACHE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("startUrl") != start_url:
                continue
            exp = data.get("expiresAt")
            if not exp:
                continue
            try:
                # AWS writes ISO 8601 with trailing 'Z'
                dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if latest is None or dt > latest:
                latest = dt
        return latest

    def logout(self, profile: str | None = None) -> LoginResult:
        # `aws sso logout` clears the SSO cache for all profiles using that session
        rc, out, err = self._run(["aws", "sso", "logout"], timeout=20)
        if rc == 0:
            return LoginResult(ok=True, message="cleared AWS SSO session cache")
        return LoginResult(ok=False, message=err or out or "logout failed")
