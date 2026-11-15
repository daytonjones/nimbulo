"""Oracle Cloud Infrastructure provider (wraps the ``oci`` CLI).

Secrets are never passed on command lines, logged or returned: only profile
names, OCIDs, regions and token expiry surface.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import settings
from ..models import AuthState, Identity, LoginResult, ProviderStatus
from . import oci_config
from .base import OutputCb, Provider

DEFAULT_REGION = "us-ashburn-1"
OCI_KEYS_DIR: Path = Path(os.path.expanduser("~/.oci/keys"))  # monkeypatchable in tests
API_CHECK_TIMEOUT = 15
SESSION_VALIDATE_TIMEOUT = 20
_AUTH_FAIL_MARKERS = ("notauthenticated", "invalidkey", "not authenticated")
# A bare "401" would match request ids / OCIDs; require the JSON status field.
_STATUS_401 = re.compile(r'"status"\s*:\s*401\b')
COMPARTMENT_CACHE_TTL = 60
COMPARTMENT_KEY = "oci.compartment"  # settings: {profile: compartment OCID}
ROOT_LABEL = "root"  # display name of the tenancy root compartment

# (profile -> (fetched_at, listing)); module-level so separate provider instances share it.
_compartment_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}
_clock = time.monotonic  # monkeypatchable in tests


@dataclass
class ConfirmationRequired(LoginResult):
    """A refusal the UI may retry after the user confirms.

    Returned (with ``ok=False``) instead of running a destructive ``oci`` command:
    retry ``login(..., overwrite=True)`` or ``logout(..., confirm=True)``.
    """


def _is_auth_failure(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _AUTH_FAIL_MARKERS) or bool(_STATUS_401.search(low))


_PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9_.@-]+")
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
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            raise TypeError("exp is not numeric")
        return datetime.fromtimestamp(int(exp), timezone.utc), ""
    except (IndexError, ValueError, KeyError, TypeError, OSError, OverflowError):
        return None, f"security token file is not a valid token: {p}"


class OCIProvider(Provider):
    key = "oci"
    label = "OCI"
    cli_tool = "oci"
    profile_label = "Profile"
    secondary_label = "Compartment"

    # --- status --------------------------------------------------------------

    @staticmethod
    def _config() -> tuple[dict[str, dict[str, str]], str]:
        """(profiles, error). A malformed ~/.oci/config yields ({}, one-line reason)."""
        try:
            return oci_config.read_profiles(), ""
        except oci_config.OCIConfigError as exc:
            return {}, str(exc)

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
        cfg, cfg_err = self._config()
        names = list(cfg)
        if not self.cli_available():
            return ProviderStatus(
                self.key, AuthState.NOT_INSTALLED, profiles=names, message="oci CLI not found on PATH"
            )
        if cfg_err:
            return ProviderStatus(self.key, AuthState.ERROR, active_profile=profile, message=cfg_err)
        target = self._pick_profile(profile, cfg)
        if not target:
            return ProviderStatus(
                self.key,
                AuthState.UNAUTHENTICATED,
                profiles=names,
                message="no OCI config found; log in with the session, profile or api-key method",
            )
        opts = cfg.get(target)
        if opts is None:
            return ProviderStatus(
                self.key,
                AuthState.UNAUTHENTICATED,
                active_profile=target,
                profiles=names,
                message=f"profile {target} not found in OCI config",
            )
        if opts.get("security_token_file"):
            return self._session_status(target, opts, names)
        return self._api_key_status(target, opts, names)

    def _session_status(self, target: str, opts: dict[str, str], names: list[str]) -> ProviderStatus:
        def result(state: AuthState, message: str = "", ident: Identity | None = None) -> ProviderStatus:
            return ProviderStatus(
                self.key, state, identity=ident, active_profile=target, profiles=names, message=message
            )

        expires, err = _token_expiry(opts["security_token_file"])
        if expires is None:
            return result(AuthState.UNAUTHENTICATED, err)
        ident = Identity(
            account=opts.get("tenancy"), expires_at=expires, extra={"region": opts.get("region"), "kind": "session"}
        )
        if expires <= datetime.now(timezone.utc):
            return result(AuthState.EXPIRED, "session token expired", ident)
        rc, out, serr = self._run(
            ["oci", "session", "validate", "--profile", target, "--local"], timeout=SESSION_VALIDATE_TIMEOUT
        )
        notes = " ".join(ln.strip() for ln in serr.splitlines() if ln.strip().startswith("WARNING"))
        if rc == 0:
            return result(AuthState.AUTHENTICATED, notes, ident)
        if rc == 124:
            return result(AuthState.ERROR, f"oci session validate timed out after {SESSION_VALIDATE_TIMEOUT}s", ident)
        msg = self._last_line(serr, out) or "session validation failed"
        return result(AuthState.EXPIRED if "expire" in msg.lower() else AuthState.UNAUTHENTICATED, msg, ident)

    def _api_key_status(self, target: str, opts: dict[str, str], names: list[str]) -> ProviderStatus:
        missing = [k for k in _API_KEY_FIELDS if not opts.get(k)]
        if missing:
            return ProviderStatus(
                self.key,
                AuthState.UNAUTHENTICATED,
                active_profile=target,
                profiles=names,
                message=f"profile {target} is missing: {', '.join(missing)}",
            )
        rc, out, err = self._run(["oci", "iam", "region", "list", "--profile", target], timeout=API_CHECK_TIMEOUT)
        if rc == 0:
            notes = " ".join(ln.strip() for ln in err.splitlines() if ln.strip().startswith("WARNING"))
            ident = Identity(
                account=opts.get("tenancy"),
                user=opts.get("user"),
                extra={"region": opts.get("region"), "kind": "api-key"},
            )
            return ProviderStatus(
                self.key, AuthState.AUTHENTICATED, identity=ident, active_profile=target, profiles=names, message=notes
            )
        msg = self._last_line(err, out) or "OCI check failed"
        if rc == 124:
            state, msg = AuthState.ERROR, f"could not reach OCI (timed out after {API_CHECK_TIMEOUT}s)"
        elif _is_auth_failure(err + out):
            state = AuthState.UNAUTHENTICATED
        else:
            state = AuthState.ERROR
        return ProviderStatus(self.key, state, active_profile=target, profiles=names, message=msg)

    @staticmethod
    def _last_line(*texts: str) -> str:
        """Most useful one-line error from oci output.

        oci prints ``ServiceError:`` followed by indented JSON; surface ``code: message``
        from it. Otherwise the last line that is neither a WARNING nor just braces.
        """
        for t in texts:
            idx = t.find("ServiceError:")
            if idx >= 0:
                brace = t.find("{", idx)
                if brace >= 0:
                    try:
                        data, _ = json.JSONDecoder().raw_decode(t[brace:])
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and (data.get("code") or data.get("message")):
                        parts = [str(data[k]).strip() for k in ("code", "message") if data.get(k)]
                        return ": ".join(parts)
        for t in texts:
            lines = [
                s
                for ln in t.splitlines()
                if (s := ln.strip()) and not s.startswith("WARNING") and s.strip("{}[](),") != ""
            ]
            if lines:
                return lines[-1]
        return ""

    # --- login / logout ------------------------------------------------------

    def login_methods(self) -> list[str]:
        return ["session", "profile", "api-key"]

    def login(self, method: str = "session", on_output: OutputCb = None, **kwargs: Any) -> LoginResult:
        if method == "session":
            profile = kwargs.get("profile") or "DEFAULT"
            cfg, cfg_err = self._config()
            if cfg_err:
                return LoginResult(ok=False, message=cfg_err)
            opts = cfg.get(profile)
            if opts is not None and not opts.get("security_token_file") and not kwargs.get("overwrite"):
                return ConfirmationRequired(
                    ok=False,
                    message=(
                        f"profile {profile} is an API-key profile: a session login would convert it to a "
                        "1-hour session profile and remove its user, fingerprint, key_file, tenancy and "
                        "pass_phrase entries. The oci CLI also rewrites ~/.oci/config without its comments. "
                        "Use a new profile name or the profile method, or confirm to continue "
                        "(CLI: --overwrite)."
                    ),
                )
            region = kwargs.get("region") or (opts or {}).get("region") or DEFAULT_REGION
            rc, output = self._popen_stream(
                ["oci", "session", "authenticate", "--profile-name", profile, "--region", region],
                on_output=on_output,
                timeout=300,
            )
            return LoginResult(
                ok=(rc == 0), message="session login complete" if rc == 0 else "session login failed", output=output
            )
        if method == "profile":
            st = self.status(kwargs.get("profile"))
            chosen = kwargs.get("profile")
            if chosen and chosen in st.profiles:
                settings.set("last_profile.oci", chosen)  # later status/compartments use this profile
            return LoginResult(ok=st.ok, message=st.message or f"profile {st.active_profile}: {st.state.value}")
        if method == "api-key":
            return self._api_key_login(on_output, **kwargs)
        return LoginResult(ok=False, message=f"unknown login method: {method}")

    @staticmethod
    def _discard(paths: list[Path]) -> None:
        for p in paths:
            try:
                p.unlink()
            except OSError:
                pass

    def api_key_defaults(self) -> dict[str, str]:
        """Pre-fill values for the api-key login form."""
        cfg, _ = self._config()
        active = self._pick_profile(None, cfg)
        region = (cfg.get(active, {}).get("region") if active else None) or DEFAULT_REGION
        profile = "my-profile" if "DEFAULT" in cfg else "DEFAULT"
        return {"profile": profile, "region": region, "key_dir": str(OCI_KEYS_DIR)}

    def _api_key_login(self, on_output: OutputCb = None, **kw: Any) -> LoginResult:
        def fail(msg: str) -> LoginResult:
            return LoginResult(ok=False, message=msg)

        name = str(kw.get("profile") or "")
        user = str(kw.get("user") or "").strip()
        tenancy = str(kw.get("tenancy") or "").strip()
        region = str(kw.get("region") or "").strip().lower()
        key_file = kw.get("key_file") or None
        generate = bool(kw.get("generate_key", False))
        overwrite = bool(kw.get("overwrite", False))

        if not _PROFILE_NAME_RE.fullmatch(name) or name in (".", ".."):
            return fail("profile name may only contain letters, digits and . _ @ - (and must not be '.' or '..')")
        if not oci_config.validate_ocid(user, "user"):
            return fail("user OCID is not valid (expected ocid1.user.<realm>..<id>)")
        if not oci_config.validate_ocid(tenancy, "tenancy"):
            return fail("tenancy OCID is not valid (expected ocid1.tenancy.<realm>..<id>)")
        if region not in oci_config.KNOWN_REGIONS:
            near = difflib.get_close_matches(region, oci_config.KNOWN_REGIONS, n=3, cutoff=0.6)
            hint = f"; did you mean {', '.join(near)}?" if near else ""
            return fail(f"unknown region {region!r}{hint}")
        if bool(key_file) == generate:
            return fail("choose exactly one: supply an existing key file or generate a new key pair")
        if key_file:
            kp = Path(str(key_file)).expanduser().resolve()
            if not kp.is_file():
                return fail(f"key file not found: {kp}")
        cfg, cfg_err = self._config()
        if cfg_err:
            return fail(cfg_err)
        if name in cfg and not overwrite:
            return fail(f"profile {name} already exists; enable overwrite to replace it")

        pub_pem = ""
        generated: list[Path] = []  # only files this call created; removed on later failure
        if generate:
            existing = [p for p in (OCI_KEYS_DIR / f"{name}.pem", OCI_KEYS_DIR / f"{name}_public.pem") if p.exists()]
            if existing:
                return fail(
                    f"key files already exist at {existing[0]}; to reuse them enter that path in the "
                    "Key file field (CLI: --key-file) instead of generating, or remove them to "
                    "generate a new key pair "
                    "(overwrite applies to the profile only)"
                )
        try:
            if generate:
                priv, pub, fingerprint = oci_config.generate_key_pair(OCI_KEYS_DIR, name)
                generated = [priv, pub]
                pub_pem = pub.read_text()
                key_path = str(priv)
            else:
                key_path = str(kp)
                fingerprint = oci_config.fingerprint_for_key(kp)
                pub_pem = oci_config.public_pem_for_key(kp)
        except FileExistsError as exc:
            return fail(f"key files already exist: {exc}")
        except (RuntimeError, OSError) as exc:
            self._discard(generated)
            return fail(f"key setup failed: {exc}")
        try:
            oci_config.write_api_key_profile(
                name,
                user=user,
                tenancy=tenancy,
                region=region,
                key_file=key_path,
                fingerprint=fingerprint,
                overwrite=overwrite,
            )
        except FileExistsError:
            self._discard(generated)
            return fail(f"profile {name} already exists; enable overwrite to replace it")
        except (ValueError, OSError, oci_config.OCIConfigError) as exc:
            self._discard(generated)
            return fail(f"could not write OCI config: {exc}")
        if on_output:
            on_output(f"wrote profile {name}; verifying")

        steps = (
            "Upload the public key in the OCI console: Identity -> Users -> your user -> "
            "API keys -> Add API key -> paste public key."
        )
        output = "\n".join(p for p in (pub_pem.strip(), steps, f"Fingerprint: {fingerprint}") if p)
        rc, out, err = self._run(["oci", "iam", "region", "list", "--profile", name], timeout=API_CHECK_TIMEOUT)
        if rc == 0:
            return LoginResult(ok=True, message=f"logged in as profile {name}", output=output)
        if rc == 124 or not _is_auth_failure(err + out):
            why = (
                f"could not reach OCI (timed out after {API_CHECK_TIMEOUT}s)"
                if rc == 124
                else f"verification failed ({self._last_line(err, out) or 'unknown error'})"
            )
            msg = (
                f"profile {name} was saved but {why}. If you have not uploaded the public key yet, "
                f"add it in the OCI console (fingerprint {fingerprint}); then use Refresh or log in again."
            )
        else:
            msg = (
                f"profile {name} was saved but OCI rejected the key. Upload the public key in the "
                f"OCI console (Identity -> Users -> your user -> API keys -> Add API key), "
                f"fingerprint {fingerprint}, then use Refresh or log in again."
            )
        return LoginResult(ok=False, message=msg, output=output)

    def logout(self, profile: str | None = None, *, confirm: bool = False) -> LoginResult:
        cfg, cfg_err = self._config()
        if cfg_err:
            return LoginResult(ok=False, message=cfg_err)
        target = self._pick_profile(profile, cfg)
        if not target or target not in cfg:
            return LoginResult(ok=False, message="no OCI profile to log out of")
        if not cfg[target].get("security_token_file"):
            return LoginResult(
                ok=False,
                message=(
                    f"profile {target} uses an API key, not a session; logout only applies to session "
                    "profiles. Remove the profile from ~/.oci/config to forget it."
                ),
            )
        if not confirm:
            # `oci session terminate` deletes the whole profile and its ~/.oci/sessions/<profile> dir.
            return ConfirmationRequired(
                ok=False,
                message=(
                    f"Logging out terminates the session AND the profile {target} will be REMOVED from "
                    f"~/.oci/config (its ~/.oci/sessions/{target} directory is deleted too). "
                    f"To use it again, log in with the session method and profile name {target}."
                ),
            )
        rc, out, err = self._run(["oci", "session", "terminate", "--profile", target], timeout=30)
        if rc == 0:
            return LoginResult(
                ok=True, message=f"terminated OCI session {target}; profile {target} was removed from ~/.oci/config"
            )
        return LoginResult(ok=False, message=self._last_line(err, out) or "logout failed")

    # --- compartments --------------------------------------------------------

    @staticmethod
    def _saved_compartments() -> dict[str, str]:
        """profile -> compartment OCID. Kept as one dict so dots in profile names are safe."""
        d = settings.get(COMPARTMENT_KEY, {})
        return dict(d) if isinstance(d, dict) else {}

    def _profile_for_compartments(self, profile: str | None = None) -> tuple[str | None, str | None]:
        """Return (profile, its tenancy OCID); the active profile unless one is given."""
        target, opts = self._compartment_target(profile)
        return target, opts.get("tenancy")

    def _compartment_target(self, profile: str | None = None) -> tuple[str | None, dict[str, str]]:
        cfg, _ = self._config()
        target = self._pick_profile(profile, cfg)
        return target, dict(cfg.get(target, {})) if target else {}

    @staticmethod
    def _build_paths(tenancy: str, items: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """Display paths from parent links. The tenancy root is shown as ``root`` and
        prefixes every path (``root/dev/team-a``). Missing parents make the node
        top-level; cycles are cut instead of looped on."""
        by_id = {i["id"]: i for i in items if isinstance(i, dict) and i.get("id")}
        out = [(tenancy, ROOT_LABEL)]
        for cid in by_id:
            if cid == tenancy:
                continue
            parts: list[str] = []
            seen: set[str] = set()
            cur: str | None = cid
            while cur and cur in by_id and cur not in seen:
                seen.add(cur)
                parts.append(str(by_id[cur].get("name") or cur))
                cur = by_id[cur].get("compartment-id")
            if cur == tenancy:
                parts.append(ROOT_LABEL)
            out.append((cid, "/".join(reversed(parts))))
        return sorted(out, key=lambda t: (t[1] != ROOT_LABEL, t[1]))

    def list_secondary(self, profile: str | None = None) -> list[tuple[str, str]]:
        profile, opts = self._compartment_target(profile)
        tenancy = opts.get("tenancy")
        if not profile or not tenancy:
            return []
        hit = _compartment_cache.get(profile)
        if hit and _clock() - hit[0] < COMPARTMENT_CACHE_TTL:
            return list(hit[1])
        rc, out, _err = self._run(
            [
                "oci",
                "iam",
                "compartment",
                "list",
                "--compartment-id",
                tenancy,
                "--compartment-id-in-subtree",
                "true",
                "--all",
                "--lifecycle-state",
                "ACTIVE",
                "--output",
                "json",
                "--profile",
                profile,
                # session profiles only authenticate with this flag (otherwise oci wants an API key)
                *(["--auth", "security_token"] if opts.get("security_token_file") else []),
            ],
            timeout=60,
        )
        if rc != 0:
            return []
        try:
            data = json.loads(out)["data"]
            if not isinstance(data, list):
                return []
            result = self._build_paths(tenancy, data)
        except (ValueError, KeyError, TypeError):
            return []
        _compartment_cache[profile] = (_clock(), result)
        saved = self._saved_compartments()
        if saved.get(profile) and saved[profile] not in {cid for cid, _ in result}:
            del saved[profile]  # stale: compartment gone or no longer ACTIVE
            settings.set(COMPARTMENT_KEY, saved)
        return list(result)

    def set_secondary(self, sid: str, profile: str | None = None) -> LoginResult:
        if profile is not None and profile not in self._config()[0]:
            return LoginResult(ok=False, message=f"profile {profile} not found in OCI config")
        profile, _ = self._profile_for_compartments(profile)
        if not profile:
            return LoginResult(ok=False, message="no OCI profile selected")
        saved = self._saved_compartments()
        saved[profile] = sid
        settings.set(COMPARTMENT_KEY, saved)
        return LoginResult(ok=True, message=f"compartment set for profile {profile}")

    def active_compartment(self, profile: str | None = None) -> str | None:
        if profile is None:
            profile, _ = self._profile_for_compartments()
        if not profile:
            return None
        return self._saved_compartments().get(profile) or None
