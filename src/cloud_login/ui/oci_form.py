"""Toolkit-independent validation for the OCI api-key login form (shared by TUI and GUI)."""
from __future__ import annotations

import difflib
import re
from typing import Optional

from ..providers import oci_config

_OCI_PROFILE_RE = re.compile(r"[A-Za-z0-9_.@-]+")


def validate_oci_form(v: dict) -> Optional[str]:
    """Return the first problem with the OCI api-key form values, or None. Plain text, not markup."""
    name = v["profile"]
    if not _OCI_PROFILE_RE.fullmatch(name) or name in (".", ".."):
        return "profile name may only contain letters, digits and . _ @ - (and must not be '.' or '..')"
    if not oci_config.validate_ocid(v["user"], "user"):
        return "user OCID is not valid (expected ocid1.user.<realm>..<id>)"
    if not oci_config.validate_ocid(v["tenancy"], "tenancy"):
        return "tenancy OCID is not valid (expected ocid1.tenancy.<realm>..<id>)"
    region = v["region"]
    if region not in oci_config.KNOWN_REGIONS:
        near = difflib.get_close_matches(region, oci_config.KNOWN_REGIONS, n=3, cutoff=0.6)
        hint = f"; did you mean {', '.join(near)}?" if near else ""
        return f"unknown region {region!r}{hint}"
    if bool(v["key_file"]) == bool(v["generate_key"]):
        return "choose exactly one: supply an existing key file or generate a new key pair"
    return None
