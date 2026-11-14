"""Helpers for the OCI CLI config file (~/.oci/config) and API signing keys.

No OCI SDK and no extra runtime dependency: key generation shells out to
``openssl``. Private key material is never logged or returned, only its path.
"""
from __future__ import annotations

import configparser
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

OCI_CONFIG_PATH: Path = Path(os.path.expanduser("~/.oci/config"))

KNOWN_REGIONS: tuple[str, ...] = (
    "us-ashburn-1", "us-phoenix-1", "us-sanjose-1", "us-chicago-1",
    "ca-toronto-1", "ca-montreal-1",
    "sa-saopaulo-1", "sa-vinhedo-1", "sa-santiago-1", "sa-bogota-1", "mx-queretaro-1", "mx-monterrey-1",
    "eu-frankfurt-1", "eu-amsterdam-1", "eu-zurich-1", "eu-marseille-1", "eu-milan-1",
    "eu-stockholm-1", "eu-paris-1", "eu-madrid-1", "eu-madrid-3",
    "uk-london-1", "uk-cardiff-1",
    "ap-tokyo-1", "ap-osaka-1", "ap-seoul-1", "ap-chuncheon-1", "ap-mumbai-1",
    "ap-hyderabad-1", "ap-sydney-1", "ap-melbourne-1", "ap-singapore-1", "ap-singapore-2",
    "me-jeddah-1", "me-dubai-1", "me-abudhabi-1",
    "af-johannesburg-1", "il-jerusalem-1",
)

OPENSSL_TIMEOUT = 30
_HEADER_RE = re.compile(r"^\[([^\]]+)\]\s*$")
_OCID_PART = r"[A-Za-z0-9_-]+"


def read_profiles() -> dict[str, dict[str, str]]:
    """Return {profile: {key: value}}; ``DEFAULT`` included. Empty if no file."""
    if not OCI_CONFIG_PATH.exists():
        return {}
    cp = configparser.RawConfigParser()
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(OCI_CONFIG_PATH)
    out: dict[str, dict[str, str]] = {}
    if cp.defaults():
        out["DEFAULT"] = dict(cp.defaults())
    for section in cp.sections():
        # items() would merge DEFAULT values in; keep only the section's own keys.
        out[section] = dict(cp._sections[section])  # type: ignore[attr-defined]
    return out


def _atomic_write_0600(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_api_key_profile(
    name: str,
    *,
    user: str,
    tenancy: str,
    region: str,
    key_file: str,
    fingerprint: str,
    overwrite: bool = False,
) -> None:
    values = {
        "user": user,
        "fingerprint": fingerprint,
        "tenancy": tenancy,
        "region": region,
        "key_file": key_file,
    }
    for v in (name, *values.values()):
        if "\n" in v or "\r" in v:
            raise ValueError("newlines are not allowed in OCI config values")
    if not name or "]" in name:
        raise ValueError("invalid profile name")
    text = ""
    if OCI_CONFIG_PATH.exists():
        with open(OCI_CONFIG_PATH, encoding="utf-8", newline="") as f:
            text = f.read()
    lines = text.splitlines(keepends=True)
    headers = [
        (i, m.group(1).strip())
        for i, line in enumerate(lines)
        if (m := _HEADER_RE.match(line))
    ]
    block = f"[{name}]\n" + "".join(f"{k}={v}\n" for k, v in values.items())
    pos = next((n for n, (_, h) in enumerate(headers) if h == name), None)
    if pos is not None:
        if not overwrite:
            raise FileExistsError(f"OCI profile {name!r} already exists")
        start = headers[pos][0]
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        # keep trailing blank/comment lines (they belong to the next section)
        keep = end
        while keep > start + 1 and (not lines[keep - 1].strip() or lines[keep - 1].lstrip()[0] in "#;"):
            keep -= 1
        new_text = "".join(lines[:start]) + block + "".join(lines[keep:])
    else:
        if text and not text.endswith(("\n", "\r")):
            text += "\n"
        sep = "\n" if text.strip() else ""
        new_text = text + sep + block
    _atomic_write_0600(OCI_CONFIG_PATH, new_text)


def validate_ocid(value: str, kind: str) -> bool:
    if kind not in ("user", "tenancy") or not isinstance(value, str):
        return False
    value = value.strip() if value else ""
    if not value:
        return False
    # ocid1.<kind>.<realm>.<region-or-empty>.<unique>
    return bool(re.fullmatch(rf"ocid1\.{kind}\.{_OCID_PART}\.(?:{_OCID_PART})?\.{_OCID_PART}", value))


def _openssl() -> str:
    exe = shutil.which("openssl")
    if not exe:
        raise RuntimeError(
            "openssl was not found on PATH; install it to generate an OCI API signing key."
        )
    return exe


def _run(args: list[str], data: bytes | None = None) -> bytes:
    try:
        r = subprocess.run(args, input=data, capture_output=True, check=True,
                           timeout=OPENSSL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"openssl timed out after {OPENSSL_TIMEOUT}s") from None
    except subprocess.CalledProcessError as e:
        # Do not include stdout (may hold key material); stderr only.
        raise RuntimeError(f"openssl failed: {e.stderr.decode(errors='replace').strip()}") from None
    return r.stdout


def _write_new(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _format_fingerprint(der: bytes) -> str:
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, 32, 2))


def fingerprint_for_key(path: Path | str) -> str:
    """MD5 fingerprint of the public half of an existing unencrypted RSA private key.

    The key is fed to openssl on stdin (never on the command line). Raises
    RuntimeError if the key cannot be read or is not a usable unencrypted RSA key.
    """
    openssl = _openssl()
    try:
        key = Path(os.path.expanduser(str(path))).read_bytes()
    except OSError as e:
        raise RuntimeError(f"cannot read key file: {e.strerror}") from None
    try:
        der = _run([openssl, "rsa", "-pubout", "-outform", "DER", "-passin", "pass:"], key)
    except RuntimeError:
        raise RuntimeError(
            "could not read the key file as an unencrypted RSA private key "
            "(passphrase-protected keys are not supported)"
        ) from None
    return _format_fingerprint(der)


def generate_key_pair(directory: Path, name: str) -> tuple[Path, Path, str]:
    """Generate an RSA-2048 API signing key. Returns (private, public, fingerprint)."""
    openssl = _openssl()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = directory / f"{name}.pem"
    pub = directory / f"{name}_public.pem"
    if priv.exists() or pub.exists():
        raise FileExistsError(f"key files for {name!r} already exist in {directory}")
    created: list[Path] = []
    try:
        key = _run([openssl, "genrsa", "2048"])
        _write_new(priv, key, 0o600)  # born 0600, no chmod-after-write
        created.append(priv)
        pub_pem = _run([openssl, "rsa", "-pubout"], key)
        _write_new(pub, pub_pem, 0o644)
        created.append(pub)
        der = _run([openssl, "rsa", "-pubout", "-outform", "DER"], key)
        fingerprint = _format_fingerprint(der)
    except BaseException:
        for f in created:  # only files this call created
            try:
                f.unlink()
            except OSError:
                pass
        raise
    return priv, pub, fingerprint
