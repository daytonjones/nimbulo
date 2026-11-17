"""Cross-UI persisted settings.

A small JSON file at ~/.config/nimbulo/settings.json, used by all UIs
(CLI/TUI/GUI/menubar) so things like the last-selected profile survive across
launches and across UI changes.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.path.expanduser("~/.config/nimbulo"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"
# This project was named "cloud-login" before it was renamed. Settings saved under the old name are
# still read (never modified) until the first save writes the new file.
LEGACY_SETTINGS_FILE = Path(os.path.expanduser("~/.config/cloud-login/settings.json"))


def _load() -> dict[str, Any]:
    source = SETTINGS_FILE if SETTINGS_FILE.exists() else LEGACY_SETTINGS_FILE
    if not source.exists():
        return {}
    try:
        data = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    """Atomically write settings, following a symlinked settings.json (dotfile managers).

    The temp file lives next to the REAL target and replaces it, so a symlink is
    preserved and the target keeps its permissions (0600 for a new file).
    """
    target = Path(os.path.realpath(SETTINGS_FILE))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = os.stat(target).st_mode & 0o777
    except OSError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(key: str, default: Any = None) -> Any:
    """Read a setting by dotted key, e.g. 'gui.window_geometry' or 'last_profile.aws'."""
    data = _load()
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set(key: str, value: Any) -> None:
    data = _load()
    cur = data
    parts = key.split(".")
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value
    _save(data)


def delete(key: str) -> None:
    data = _load()
    cur = data
    parts = key.split(".")
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)
        _save(data)
