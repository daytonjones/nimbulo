"""State saved under the old name (cloud-login) is still found after the rename to nimbulo."""

import json

import keyring
import pytest
from typer.testing import CliRunner

from nimbulo import secrets, settings
from nimbulo.ui.cli import app

runner = CliRunner()


def test_legacy_settings_are_read_and_not_modified(_isolate_home):
    legacy = settings.LEGACY_SETTINGS_FILE
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"last_profile": {"aws": "work"}, "providers": {"disabled": ["vultr"]}}))
    before = legacy.read_text()
    assert settings.get("last_profile.aws") == "work"
    assert not settings.SETTINGS_FILE.exists()
    settings.set("gui.x", 1)  # first save writes the NEW file, carrying the old values over
    new = json.loads(settings.SETTINGS_FILE.read_text())
    assert new == {"last_profile": {"aws": "work"}, "providers": {"disabled": ["vultr"]}, "gui": {"x": 1}}
    assert legacy.read_text() == before  # old file untouched


def test_new_settings_win_over_legacy(_isolate_home):
    settings.LEGACY_SETTINGS_FILE.parent.mkdir(parents=True)
    settings.LEGACY_SETTINGS_FILE.write_text(json.dumps({"a": "old"}))
    settings.SETTINGS_FILE.parent.mkdir(parents=True)
    settings.SETTINGS_FILE.write_text(json.dumps({"a": "new"}))
    assert settings.get("a") == "new"


def test_legacy_keyring_token_is_found_and_copied(_isolate_home):
    keyring.set_password("cloud-login", "vultr", "tok")
    assert secrets.get("vultr") == "tok"
    assert keyring.get_password("nimbulo", "vultr") == "tok"  # copied to the new service
    assert keyring.get_password("cloud-login", "vultr") == "tok"  # old entry left in place


def test_new_keyring_token_preferred(_isolate_home):
    keyring.set_password("cloud-login", "vultr", "old")
    keyring.set_password("nimbulo", "vultr", "new")
    assert secrets.get("vultr") == "new"


def test_delete_removes_both(_isolate_home):
    keyring.set_password("cloud-login", "do", "a")
    keyring.set_password("nimbulo", "do", "b")
    secrets.delete("do")
    assert secrets.get("do") is None


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="autostart is macOS-only")
def test_autostart_install_removes_legacy_agent(_isolate_home, monkeypatch):
    from pathlib import Path

    from nimbulo.ui import cli

    home = Path(_isolate_home)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    calls = []
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0, "stderr": ""})()
    )
    monkeypatch.setattr(cli, "_kill_running_menubar", lambda: None)
    legacy = home / "Library/LaunchAgents/com.cloud-login.menubar.plist"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("<plist/>")
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 0, r.output
    assert not legacy.exists()
    assert ["launchctl", "unload", "-w", str(legacy)] in calls
    assert (home / "Library/LaunchAgents/com.nimbulo.menubar.plist").exists()
