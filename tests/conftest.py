"""Shared test fixtures.

``fake_run`` replaces every subprocess call made through ``Provider._run`` and
``Provider._popen_stream`` with a lookup in a command table, so tests never touch
real CLIs or the network.

Usage::

    def test_x(fake_run):
        fake_run.add(["oci", "iam", "region", "list", "--profile", "P"], rc=0, out="{}")
        ...
        assert fake_run.calls == [["oci", "iam", "region", "list", "--profile", "P"]]

* ``add(cmd, rc=0, out="", err="", prefix=False)`` registers a response. A call
  matches only the EXACT command. With ``prefix=True`` the entry also matches any
  command that starts with ``cmd`` (the longest such prefix wins); use it only when
  the full argv genuinely does not matter to the test. Unregistered commands
  return ``(127, "", "no fake for ...")``.
* ``calls`` records every command run (``_run`` and streamed alike);
  ``streamed`` records only the streamed ones.
* Streamed commands feed ``out`` then ``err`` lines to ``on_output`` and return
  ``(rc, combined)``.
* ``installed`` (default True) controls whether ``shutil.which("oci")`` finds a
  CLI; set ``fake_run.installed = False`` to simulate it missing. Other tools
  resolve normally.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cloud_login import settings
from cloud_login.providers import aws, digitalocean, gcp, oci, oci_config, vultr
from cloud_login.providers.base import Provider

# --- global isolation guard -------------------------------------------------------
# Every test runs against a throwaway HOME. Module-level paths (computed at import
# time from the real ~) are repointed, and the real `oci` CLI cannot be executed.
# Tests may still override any of these with their own monkeypatch.

_REAL_HOME = Path(os.path.expanduser("~"))


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        try:
            st = p.lstat()
        except OSError:
            continue
        out[str(p)] = (st.st_mtime_ns, st.st_size)
    return out


_REAL_OCI_BEFORE = _snapshot(_REAL_HOME / ".oci")


def pytest_sessionfinish(session, exitstatus):
    after = _snapshot(_REAL_HOME / ".oci")
    if after != _REAL_OCI_BEFORE:
        changed = sorted(set(after.items()) ^ set(_REAL_OCI_BEFORE.items()))[:5]
        raise pytest.UsageError(f"tests modified the real ~/.oci (first differences: {changed})")


class RealOCIBlocked(AssertionError):
    pass


def _is_oci(cmd) -> bool:
    argv = cmd if isinstance(cmd, (list, tuple)) else [cmd]
    return bool(argv) and os.path.basename(str(argv[0])) == "oci"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OCI_CLI_CONFIG_FILE", raising=False)
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", home / ".oci" / "config")
    monkeypatch.setattr(oci, "OCI_KEYS_DIR", home / ".oci" / "keys")
    monkeypatch.setattr(aws, "AWS_DIR", home / ".aws")
    monkeypatch.setattr(aws, "CONFIG_FILE", home / ".aws" / "config")
    monkeypatch.setattr(aws, "CREDENTIALS_FILE", home / ".aws" / "credentials")
    monkeypatch.setattr(aws, "SSO_CACHE_DIR", home / ".aws" / "sso" / "cache")
    monkeypatch.setattr(gcp, "GCLOUD_DIR", home / ".config" / "gcloud")
    monkeypatch.setattr(vultr, "VULTR_CLI_CONFIG", home / ".vultr-cli.yaml")
    monkeypatch.setattr(digitalocean, "DOCTL_CONFIG", home / ".config" / "doctl" / "config.yaml")
    monkeypatch.setattr(settings, "CONFIG_DIR", home / ".config" / "cloud-login")
    monkeypatch.setattr(settings, "SETTINGS_FILE", home / ".config" / "cloud-login" / "settings.json")

    real_run, real_popen = subprocess.run, subprocess.Popen

    def guarded_run(cmd, *a, **kw):
        if _is_oci(cmd):
            raise RealOCIBlocked(f"test tried to run the real oci CLI: {cmd!r} (use the fake_run fixture)")
        return real_run(cmd, *a, **kw)

    class GuardedPopen(real_popen):
        def __init__(self, cmd, *a, **kw):
            if _is_oci(cmd):
                raise RealOCIBlocked(f"test tried to run the real oci CLI: {cmd!r} (use the fake_run fixture)")
            super().__init__(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    return home


class FakeRun:
    def __init__(self) -> None:
        self.table: dict[tuple[str, ...], tuple[int, str, str]] = {}
        self.prefixes: set[tuple[str, ...]] = set()
        self.calls: list[list[str]] = []
        self.streamed: list[list[str]] = []
        self.installed = True
        self.no_tty: list[bool] = []  # per _run call: was it detached from the terminal

    def add(self, cmd: list[str], rc: int = 0, out: str = "", err: str = "", prefix: bool = False) -> None:
        self.table[tuple(cmd)] = (rc, out, err)
        if prefix:
            self.prefixes.add(tuple(cmd))
        else:
            self.prefixes.discard(tuple(cmd))

    def _lookup(self, cmd: list[str]) -> tuple[int, str, str]:
        key = tuple(cmd)
        if key in self.table:
            return self.table[key]
        best = None
        for k in self.prefixes:
            if key[: len(k)] == k and (best is None or len(k) > len(best)):
                best = k
        if best is not None:
            return self.table[best]
        return 127, "", f"no fake for {cmd}"


@pytest.fixture
def fake_run(monkeypatch) -> FakeRun:
    fake = FakeRun()

    def _run(cmd, timeout=60, input_text=None, no_tty=False):
        fake.calls.append(list(cmd))
        fake.no_tty.append(no_tty)
        return fake._lookup(cmd)

    def _popen_stream(cmd, on_output=None, timeout=600):
        fake.calls.append(list(cmd))
        fake.streamed.append(list(cmd))
        rc, out, err = fake._lookup(cmd)
        lines = [ln for ln in (out + "\n" + err).splitlines() if ln]
        for ln in lines:
            if on_output:
                on_output(ln)
        return rc, "\n".join(lines)

    real_which = shutil.which

    def _which(name, *a, **kw):
        if name == "oci":
            return "/fake/bin/oci" if fake.installed else None
        return real_which(name, *a, **kw)

    monkeypatch.setattr(Provider, "_run", staticmethod(_run))
    monkeypatch.setattr(Provider, "_popen_stream", staticmethod(_popen_stream))
    monkeypatch.setattr(shutil, "which", _which)
    return fake
