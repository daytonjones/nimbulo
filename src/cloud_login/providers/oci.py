"""Oracle Cloud Infrastructure provider (wraps the ``oci`` CLI).

Secrets are never passed on command lines, logged or returned: only profile
names, OCIDs, regions and token expiry surface.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import settings
from ..models import AuthState, Identity, LoginResult, ProviderStatus
from . import oci_config
from .base import OutputCb, Provider

DEFAULT_REGION = "us-ashburn-1"
API_CHECK_TIMEOUT = 15
_AUTH_FAIL_MARKERS = ("notauthenticated", "401", "invalidkey", "not authenticated")
_API_KEY_FIELDS = ("user", "tenancy", "fingerprint", "key_file")


def _token_expiry(path: str) -> tuple[datetime | None, str]:
    """Return (expiry, error). Decodes the JWT payload only; no signature check."""
    p = Path(os.path.expanduser(path))
    try:
        token = p.read_text().strip()
    except OSError:
        return None, f"security token file not readable: {p}"
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return datetime.fromtimestamp(int(exp), timezone.utc), ""
    except (IndexError, ValueError, KeyError, TypeError, OSError):
        return None, f"security token file is not a valid token: {p}"


class OCIProvider(Provider):
    key = "oci"
    label = "OCI"
    cli_tool = "oci"
    profile_label = "Profile"
    secondary_label = "Compartment"

    # --- status --------------------------------------------------------------

    def _pick_profile(self, profile: str | None, cfg: dict[str, dict[str, str]]) -> str | None:
        if profile:
            return profile
        last = settings.get("last_profile.oci")
        if last and last in cfg:
            return last
        if "DEFAULT" in cfg:
            return "DEFAULT"
        return next(iter(cfg), None)

    def status(self, profile: str | None = None) -> ProviderStatus:
        cfg = oci_config.read_profiles()
        names = list(cfg)
        if not self.cli_available():
            return ProviderStatus(self.key, AuthState.NOT_INSTALLED, profiles=names,
                                  message="oci CLI not found on PATH")
        target = self._pick_profile(profile, cfg)
        if not target:
            return ProviderStatus(
                self.key, AuthState.UNAUTHENTICATED, profiles=names,
                message="no OCI config found; log in with the session, profile or api-key method",
            )
        opts = cfg.get(target)
        if opts is None:
            return ProviderStatus(self.key, AuthState.UNAUTHENTICATED, active_profile=target,
                                  profiles=names, message=f"profile {target} not found in OCI config")
        if opts.get("security_token_file"):
            return self._session_status(target, opts, names)
        return self._api_key_status(target, opts, names)

    def _session_status(self, target: str, opts: dict[str, str], names: list[str]) -> ProviderStatus:
        def result(state: AuthState, message: str = "", ident: Identity | None = None) -> ProviderStatus:
            return ProviderStatus(self.key, state, identity=ident, active_profile=target,
                                  profiles=names, message=message)

        expires, err = _token_expiry(opts["security_token_file"])
        if expires is None:
            return result(AuthState.UNAUTHENTICATED, err)
        ident = Identity(account=opts.get("tenancy"), expires_at=expires,
                         extra={"region": opts.get("region"), "kind": "session"})
        if expires <= datetime.now(timezone.utc):
            return result(AuthState.EXPIRED, "session token expired", ident)
        rc, out, serr = self._run(["oci", "session", "validate", "--profile", target, "--local"], timeout=20)
        notes = " ".join(ln.strip() for ln in serr.splitlines() if ln.strip().startswith("WARNING"))
        if rc == 0:
            return result(AuthState.AUTHENTICATED, notes, ident)
        msg = self._last_line(serr, out) or "session validation failed"
        return result(AuthState.EXPIRED if "expire" in msg.lower() else AuthState.UNAUTHENTICATED, msg, ident)

    def _api_key_status(self, target: str, opts: dict[str, str], names: list[str]) -> ProviderStatus:
        missing = [k for k in _API_KEY_FIELDS if not opts.get(k)]
        if missing:
            return ProviderStatus(self.key, AuthState.UNAUTHENTICATED, active_profile=target, profiles=names,
                                  message=f"profile {target} is missing: {', '.join(missing)}")
        rc, out, err = self._run(["oci", "iam", "region", "list", "--profile", target],
                                 timeout=API_CHECK_TIMEOUT)
        if rc == 0:
            notes = " ".join(ln.strip() for ln in err.splitlines() if ln.strip().startswith("WARNING"))
            ident = Identity(account=opts.get("tenancy"), user=opts.get("user"),
                             extra={"region": opts.get("region"), "kind": "api-key"})
            return ProviderStatus(self.key, AuthState.AUTHENTICATED, identity=ident,
                                  active_profile=target, profiles=names, message=notes)
        msg = self._last_line(err, out) or "OCI check failed"
        if rc == 124:
            state, msg = AuthState.ERROR, f"could not reach OCI (timed out after {API_CHECK_TIMEOUT}s)"
        elif any(m in (err + out).lower() for m in _AUTH_FAIL_MARKERS):
            state = AuthState.UNAUTHENTICATED
        else:
            state = AuthState.ERROR
        return ProviderStatus(self.key, state, active_profile=target, profiles=names, message=msg)

    @staticmethod
    def _last_line(*texts: str) -> str:
        for t in texts:
            lines = [ln.strip() for ln in t.splitlines() if ln.strip() and not ln.startswith("WARNING")]
            if lines:
                return lines[-1]
        return ""

    # --- login / logout ------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["session", "profile", "api-key"]

    def login(self, method: str = "session", on_output: OutputCb = None, **kwargs: Any) -> LoginResult:
        if method == "session":
            profile = kwargs.get("profile") or "DEFAULT"
            region = kwargs.get("region") or oci_config.read_profiles().get(profile, {}).get("region") or DEFAULT_REGION
            rc, output = self._popen_stream(
                ["oci", "session", "authenticate", "--profile-name", profile, "--region", region],
                on_output=on_output, timeout=300,
            )
            return LoginResult(ok=(rc == 0),
                               message="session login complete" if rc == 0 else "session login failed",
                               output=output)
        if method == "profile":
            st = self.status(kwargs.get("profile"))
            return LoginResult(ok=st.ok, message=st.message or f"profile {st.active_profile}: {st.state.value}")
        if method == "api-key":
            return LoginResult(ok=False, message="api-key login not implemented yet")
        return LoginResult(ok=False, message=f"unknown login method: {method}")

    def logout(self, profile: str | None = None) -> LoginResult:
        cfg = oci_config.read_profiles()
        target = self._pick_profile(profile, cfg)
        if not target or target not in cfg:
            return LoginResult(ok=False, message="no OCI profile to log out of")
        if not cfg[target].get("security_token_file"):
            return LoginResult(ok=False, message=(
                f"profile {target} uses an API key, not a session; logout only applies to session "
                "profiles. Remove the profile from ~/.oci/config to forget it."))
        rc, out, err = self._run(["oci", "session", "terminate", "--profile", target], timeout=30)
        if rc == 0:
            return LoginResult(ok=True, message=f"terminated OCI session {target}")
        return LoginResult(ok=False, message=self._last_line(err, out) or "logout failed")
