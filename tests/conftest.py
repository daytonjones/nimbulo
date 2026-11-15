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

import shutil

import pytest

from cloud_login.providers.base import Provider


class FakeRun:
    def __init__(self) -> None:
        self.table: dict[tuple[str, ...], tuple[int, str, str]] = {}
        self.prefixes: set[tuple[str, ...]] = set()
        self.calls: list[list[str]] = []
        self.streamed: list[list[str]] = []
        self.installed = True

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

    def _run(cmd, timeout=60, input_text=None):
        fake.calls.append(list(cmd))
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
