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
    "us-ashburn-1",
    "us-phoenix-1",
    "us-sanjose-1",
    "us-chicago-1",
    "ca-toronto-1",
    "ca-montreal-1",
    "sa-saopaulo-1",
    "sa-vinhedo-1",
    "sa-santiago-1",
    "sa-bogota-1",
    "mx-queretaro-1",
    "mx-monterrey-1",
    "eu-frankfurt-1",
    "eu-amsterdam-1",
    "eu-zurich-1",
    "eu-marseille-1",
    "eu-milan-1",
    "eu-stockholm-1",
    "eu-paris-1",
    "eu-madrid-1",
    "eu-madrid-3",
    "uk-london-1",
    "uk-cardiff-1",
    "ap-tokyo-1",
    "ap-osaka-1",
    "ap-seoul-1",
    "ap-chuncheon-1",
    "ap-mumbai-1",
    "ap-hyderabad-1",
    "ap-sydney-1",
    "ap-melbourne-1",
    "ap-singapore-1",
    "ap-singapore-2",
    "me-jeddah-1",
    "me-dubai-1",
    "me-abudhabi-1",
    "af-johannesburg-1",
    "il-jerusalem-1",
)

OPENSSL_TIMEOUT = 30
# configparser's own section-header grammar (RawConfigParser.SECTCRE), applied to the
# stripped line: greedy, unanchored at the end, header name taken verbatim.
_HEADER_RE = re.compile(r"\[(?P<header>.+)\]")
_OCID_PART = r"[A-Za-z0-9_-]+"


class OCIConfigError(Exception):
    """~/.oci/config exists but cannot be read or parsed. ``str()`` is a one-line reason."""


def _short_reason(exc: Exception) -> str:
    if isinstance(exc, configparser.DuplicateSectionError):
        return f"duplicate section [{exc.section}], line {exc.lineno}"
    if isinstance(exc, configparser.DuplicateOptionError):
        return f"duplicate key {exc.option!r} in [{exc.section}], line {exc.lineno}"
    if isinstance(exc, configparser.MissingSectionHeaderError):
        return f"no [section] header before line {exc.lineno}"
    if isinstance(exc, configparser.ParsingError):
        errors = getattr(exc, "errors", None) or []
        if errors:
            return f"unparsable line {errors[0][0]}"
    if isinstance(exc, UnicodeDecodeError):
        return "file is not valid UTF-8 text"
    if isinstance(exc, OSError):
        return f"cannot read file ({exc.strerror or exc.__class__.__name__})"
    first = str(exc).strip().splitlines()
    return first[0] if first else exc.__class__.__name__


def read_profiles() -> dict[str, dict[str, str]]:
    """Return {profile: {key: value}}; ``DEFAULT`` included. Empty if no file.

    Section values are MERGED with ``[DEFAULT]`` (as the oci CLI does). Raises
    OCIConfigError if the file exists but cannot be read or parsed.
    """
    if not OCI_CONFIG_PATH.exists() and not OCI_CONFIG_PATH.is_symlink():
        return {}
    cp = configparser.RawConfigParser()
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    try:
        with open(OCI_CONFIG_PATH, encoding="utf-8") as f:
            cp.read_file(f)
    except (configparser.Error, OSError, UnicodeDecodeError) as exc:
        raise OCIConfigError(f"{OCI_CONFIG_PATH} could not be parsed: {_short_reason(exc)}") from None
    out: dict[str, dict[str, str]] = {}
    if cp.defaults():
        out["DEFAULT"] = dict(cp.defaults())
    for section in cp.sections():
        out[section] = dict(cp.items(section))  # DEFAULT values merged in, section wins
    return out


def _section_headers(lines: list[str]) -> list[tuple[int, str]]:
    """(line index, section name) for every line configparser treats as a header.

    Mirrors RawConfigParser with default settings: full-line ``#``/``;`` comments are
    skipped, a line indented deeper than the current option is a continuation, and
    the header regex is matched against the stripped line.
    """
    out: list[tuple[int, str]] = []
    in_section = has_option = False
    indent = 0
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s[0] in "#;":
            continue
        cur = len(raw) - len(raw.lstrip())
        if in_section and has_option and cur > indent:
            continue  # continuation of the previous value
        indent = cur
        m = _HEADER_RE.match(s)
        if m:
            out.append((i, m.group("header")))
            in_section, has_option = True, False
        elif "=" in s or ":" in s:
            has_option = True
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
    # Resolve symlinks first so the temp file lives next to (and replaces) the real target.
    target = OCI_CONFIG_PATH.resolve()
    text = ""
    if target.exists():
        with open(target, encoding="utf-8", newline="") as f:
            text = f.read()
    lines = text.splitlines(keepends=True)
    headers = _section_headers(lines)
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
    _atomic_write_0600(target, new_text)


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
        raise RuntimeError("openssl was not found on PATH; install it to generate an OCI API signing key.")
    return exe


def _run(args: list[str], data: bytes | None = None) -> bytes:
    try:
        r = subprocess.run(args, input=data, capture_output=True, check=True, timeout=OPENSSL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"openssl timed out after {OPENSSL_TIMEOUT}s") from None
    except subprocess.CalledProcessError as e:
        # Do not include stdout (may hold key material); stderr only.
        raise RuntimeError(f"openssl failed: {e.stderr.decode(errors='replace').strip()}") from None
    return r.stdout


def _write_new(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)  # raises if it exists: never ours to remove
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        try:
            path.unlink()  # only the file this call just created
        except OSError:
            pass
        raise


def _format_fingerprint(der: bytes) -> str:
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, 32, 2))


def _public_from_private(path: Path | str, der: bool) -> bytes:
    """Public half of an existing unencrypted RSA private key, via openssl on stdin."""
    openssl = _openssl()
    try:
        key = Path(os.path.expanduser(str(path))).read_bytes()
    except OSError as e:
        raise RuntimeError(f"cannot read key file: {e.strerror}") from None
    args = [openssl, "rsa", "-pubout", *(["-outform", "DER"] if der else []), "-passin", "pass:"]
    try:
        return _run(args, key)
    except RuntimeError:
        raise RuntimeError(
            "could not read the key file as an unencrypted RSA private key "
            "(passphrase-protected keys are not supported)"
        ) from None


def fingerprint_for_key(path: Path | str) -> str:
    """MD5 fingerprint of the public half of an existing unencrypted RSA private key.

    The key is fed to openssl on stdin (never on the command line). Raises
    RuntimeError if the key cannot be read or is not a usable unencrypted RSA key.
    """
    return _format_fingerprint(_public_from_private(path, der=True))


def public_pem_for_key(path: Path | str) -> str:
    """PEM public key for an existing unencrypted RSA private key (same rules as above)."""
    return _public_from_private(path, der=False).decode("ascii")


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
