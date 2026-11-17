"""Regression tests: API tokens stay out of argv; menubar logs are not in /tmp."""

import os
import stat
import sys

import pytest
from typer.testing import CliRunner

from nimbulo import providers
from nimbulo.models import LoginResult
from nimbulo.ui import cli
from nimbulo.ui.cli import app

runner = CliRunner()


class StubProvider:
    key = "vultr"

    def __init__(self):
        self.kwargs = None

    def login_methods(self):
        return ["api-key"]

    def login(self, **kwargs):
        self.kwargs = kwargs
        return LoginResult(ok=True, message="ok")


@pytest.fixture
def stub(monkeypatch):
    s = StubProvider()
    monkeypatch.setattr(providers, "get", lambda key: s)
    monkeypatch.delenv("NIMBULO_TOKEN", raising=False)
    return s


def test_set_key_reads_token_from_stdin(stub):
    r = runner.invoke(app, ["set-key", "vultr", "--token-stdin"], input="s3cret\n")
    assert r.exit_code == 0, r.output
    assert stub.kwargs["token"] == "s3cret"
    assert "s3cret" not in r.output


def test_login_reads_token_from_env(stub, monkeypatch):
    monkeypatch.setenv("NIMBULO_TOKEN", "envtok")
    r = runner.invoke(app, ["login", "vultr"])
    assert r.exit_code == 0, r.output
    assert stub.kwargs["token"] == "envtok"


def test_token_flag_still_works_but_warns(stub):
    r = runner.invoke(app, ["login", "vultr", "--token", "flagtok"])
    assert r.exit_code == 0, r.output
    assert stub.kwargs["token"] == "flagtok"
    assert "process list" in r.output


def test_token_flag_hidden_from_help():
    r = runner.invoke(app, ["login", "--help"])
    assert "--token-stdin" in r.output
    assert "--token " not in r.output.replace("--token-stdin", "")


@pytest.mark.skipif(sys.platform != "darwin", reason="autostart is macOS-only")
def test_autostart_logs_not_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setattr(cli, "_kill_running_menubar", lambda: None)
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 0, r.output
    plist = (tmp_path / "Library/LaunchAgents/com.nimbulo.menubar.plist").read_text()
    log_dir = tmp_path / "Library/Logs/nimbulo"
    assert "/tmp/" not in plist
    assert f"{log_dir}/menubar.log" in plist
    assert stat.S_IMODE(os.stat(log_dir).st_mode) == 0o700
