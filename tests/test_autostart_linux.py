"""`nimbulo autostart` on Linux (XDG autostart entry). Runs on any OS: sys.platform is patched
and every subprocess call is faked, so no real pkill/pgrep/launchctl/Popen ever happens."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nimbulo.ui import cli
from nimbulo.ui.cli import app

runner = CliRunner()


class Spy:
    def __init__(self) -> None:
        self.runs: list[list[str]] = []
        self.popens: list[tuple[list[str], dict]] = []
        self.pgrep_rc = 1


@pytest.fixture
def linux(monkeypatch, _isolate_home):
    # Patching sys.platform is process-wide; pre-import modules that typer/pydantic load lazily
    # (zoneinfo -> sysconfig breaks under a fake platform).
    import zoneinfo  # noqa: F401

    from pydantic import fields  # noqa: F401

    monkeypatch.setattr("nimbulo.ui.cli.sys.platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    spy = Spy()

    def fake_run(cmd, *a, **kw):
        spy.runs.append(list(cmd))
        rc = spy.pgrep_rc if cmd[0] == "pgrep" else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

    class FakePopen:
        def __init__(self, cmd, *a, **kw):
            spy.popens.append((list(cmd), kw))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(cli, "_resolve_nimbulo_program", lambda: ["/opt/my apps/bin/nimbulo", "menubar"])
    spy.home = _isolate_home
    return spy


def _entry(home: Path) -> Path:
    return home / ".config" / "autostart" / "nimbulo-menubar.desktop"


def test_install_writes_entry_with_quoted_exec(linux):
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 0, r.output
    text = _entry(linux.home).read_text()
    assert "[Desktop Entry]" in text
    assert "Type=Application" in text
    assert 'Exec="/opt/my apps/bin/nimbulo" menubar' in text
    assert "Terminal=false" in text
    assert "X-GNOME-Autostart-enabled=true" in text


def test_exec_quoting_escapes_specials():
    q = cli._desktop_exec_quote
    assert q("plain") == "plain"
    assert q("a b") == '"a b"'
    assert q('a"b') == '"a\\\\"b"'.replace("\\\\", "\\")  # "a\"b"
    assert q("$x") == '"\\$x"'
    assert q("`x`") == '"\\`x\\`"'
    # a literal backslash needs two quoting layers (Exec escape + desktop string escape)
    assert q("a\\b") == '"a\\\\\\\\b"'
    assert q("100%") == "100%%"


def test_install_honors_xdg_config_home(linux, monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 0, r.output
    assert (xdg / "autostart" / "nimbulo-menubar.desktop").exists()
    assert not _entry(linux.home).exists()


def test_install_idempotent_and_mode(linux):
    for _ in range(2):
        assert runner.invoke(app, ["autostart", "install"]).exit_code == 0
    p = _entry(linux.home)
    assert stat.S_IMODE(p.stat().st_mode) == 0o644
    assert p.read_text().count("[Desktop Entry]") == 1
    assert [f.name for f in p.parent.iterdir()] == ["nimbulo-menubar.desktop"]  # no temp leftovers


def test_install_without_graphical_session_does_not_start(linux):
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 0
    assert linux.popens == []
    assert "next login" in r.output


def test_install_with_display_kills_then_starts_detached(linux, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 0, r.output
    assert any(c[:2] == ["pkill", "-f"] for c in linux.runs)
    assert len(linux.popens) == 1
    cmd, kw = linux.popens[0]
    assert cmd == ["/opt/my apps/bin/nimbulo", "menubar"]
    assert kw["start_new_session"] is True
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL


def test_install_with_wayland_starts(linux, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert runner.invoke(app, ["autostart", "install"]).exit_code == 0
    assert len(linux.popens) == 1


def test_uninstall_removes_and_is_idempotent(linux):
    runner.invoke(app, ["autostart", "install"])
    r = runner.invoke(app, ["autostart", "uninstall"])
    assert r.exit_code == 0
    assert not _entry(linux.home).exists()
    assert any(c[:2] == ["pkill", "-f"] for c in linux.runs)
    r = runner.invoke(app, ["autostart", "uninstall"])
    assert r.exit_code == 0
    assert "already uninstalled" in r.output


def test_status_not_installed_and_not_running(linux):
    r = runner.invoke(app, ["autostart", "status"])
    assert r.exit_code == 0
    assert "not installed" in r.output
    assert "not running" in r.output


def test_status_installed_and_running(linux):
    runner.invoke(app, ["autostart", "install"])
    linux.pgrep_rc = 0
    r = runner.invoke(app, ["autostart", "status"])
    assert "not installed" not in r.output
    assert "installed" in r.output
    assert "nimbulo-menubar.desktop" in r.output
    assert "not running" not in r.output
    assert "running" in r.output


def test_status_tolerates_missing_pgrep(linux, monkeypatch):
    def boom(cmd, *a, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", boom)
    r = runner.invoke(app, ["autostart", "status"])
    assert r.exit_code == 0, r.output


@pytest.mark.parametrize("platform", ["win32", "freebsd14"])
def test_unsupported_platform_exits_2(monkeypatch, platform):
    monkeypatch.setattr("nimbulo.ui.cli.sys.platform", platform)
    r = runner.invoke(app, ["autostart", "install"])
    assert r.exit_code == 2
    assert "macOS and Linux" in r.output


def test_linux_unknown_action_exits_2(linux):
    r = runner.invoke(app, ["autostart", "bogus"])
    assert r.exit_code == 2
