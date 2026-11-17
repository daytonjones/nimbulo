"""settings.json written through a symlink (dotfile managers) keeps the link and the target's mode."""

import json
import os
import stat

from nimbulo import settings


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_symlinked_settings_file_is_written_through(tmp_path, monkeypatch):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "nimbulo-settings.json"
    real.write_text(json.dumps({"keep": 1}))
    real.chmod(0o644)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    link = cfg / "settings.json"
    link.symlink_to(real)
    monkeypatch.setattr(settings, "CONFIG_DIR", cfg)
    monkeypatch.setattr(settings, "SETTINGS_FILE", link)

    settings.set("providers.disabled", ["vultr"])

    assert link.is_symlink() and os.readlink(link) == str(real)
    assert json.loads(real.read_text()) == {"keep": 1, "providers": {"disabled": ["vultr"]}}
    assert _mode(real) == 0o644  # target's mode preserved
    assert sorted(p.name for p in dotfiles.iterdir()) == ["nimbulo-settings.json"]  # no temp left
    assert sorted(p.name for p in cfg.iterdir()) == ["settings.json"]


def test_dangling_symlink_creates_the_target(tmp_path, monkeypatch):
    real = tmp_path / "dotfiles" / "s.json"  # parent does not exist yet
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    link = cfg / "settings.json"
    link.symlink_to(real)
    monkeypatch.setattr(settings, "CONFIG_DIR", cfg)
    monkeypatch.setattr(settings, "SETTINGS_FILE", link)
    settings.set("a", 1)
    assert link.is_symlink() and json.loads(real.read_text()) == {"a": 1}
    assert _mode(real) == 0o600


def test_new_regular_file_is_private_and_existing_mode_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    settings.set("a", 1)
    assert _mode(tmp_path / "settings.json") == 0o600
    (tmp_path / "settings.json").chmod(0o640)
    settings.set("b", 2)
    assert _mode(tmp_path / "settings.json") == 0o640
