"""Cross-UI persisted settings.

A small JSON file at ~/.config/cloud-login/settings.json, used by all UIs
(CLI/TUI/GUI/menubar) so things like the last-selected profile survive across
launches and across UI changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.path.expanduser("~/.config/cloud-login"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"


def _load() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))


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
