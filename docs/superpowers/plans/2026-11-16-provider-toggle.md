# Provider Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let the user enable/disable cloud providers via `settings.json` or inside the CLI/TUI/GUI/menubar, saved and applied live.

**Architecture:** One setting (`providers.disabled`, a list) read by `providers.all_providers()`; `providers.get()` is unchanged. UIs add a Providers control that calls `providers.set_enabled` and rebuilds their provider list live.

**Tech Stack:** Python 3.12, typer, Textual, PySide6, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-provider-toggle-design.md`

## Global Constraints

- Default behaviour unchanged: missing/empty `providers.disabled` = all six providers enabled, same order (aws, gcp, azure, oci, vultr, digitalocean).
- `providers.get(key)` ignores the disabled list (explicit use always works).
- At least one provider must stay enabled; disabling the last one is refused with a clear message and changes nothing.
- Disabling never deletes credentials, profiles or other settings.
- Unknown keys in the list are ignored; a non-list value or malformed settings is treated as "nothing disabled".
- No behaviour change to any provider class. Tests never touch real HOME (the autouse `_isolate_home` fixture already enforces it); GUI tests use `QT_QPA_PLATFORM=offscreen`.
- `ruff check src tests` clean; full `pytest` green after every task.

## Review Focus

- Hand-edited `settings.json` with `"providers": {"disabled": "aws"}` (string) or unknown key: must not crash; treated as nothing/ignored.
- All six disabled via hand-editing the file: UIs must not crash (TUI indexes `[0]`); fall back to showing all providers and say why.
- Disabling the provider whose tab is currently selected/active: UI must move to another tab.
- Re-enabling restores the provider in its original position, not at the end.
- Settings file written while another UI is running: UIs re-read on open/refresh, no stale state in the toggle dialog.

---

### Task 1: Core API and CLI

**Files:**
- Modify: `src/nimbulo/providers/__init__.py`
- Modify: `src/nimbulo/ui/cli.py` (new `providers` sub-app; `status` default list)
- Create: `tests/test_provider_toggle.py`

**Interfaces:**
- Produces (used by every UI task):
  - `providers.all_providers(include_disabled: bool = False) -> list[Provider]` — canonical order, enabled only by default. If every provider ends up disabled (hand-edited file), returns all of them.
  - `providers.is_enabled(key: str) -> bool`
  - `providers.disabled_keys() -> list[str]` — valid keys only, canonical order.
  - `providers.set_enabled(key: str, enabled: bool) -> tuple[bool, str]` — `(ok, message)`; unknown key or disabling the last enabled provider returns `(False, reason)` without writing. Persists `providers.disabled` via `settings.set`.
  - `providers.get(name)` unchanged.

- [ ] **Step 1: Tests (failing)** in `tests/test_provider_toggle.py`: default returns six keys in order; after `set_enabled("vultr", False)` `all_providers()` has five and `include_disabled=True` six; `get("vultr")` still works; disabling all six one by one ends with the last refused and unchanged settings; re-enable restores original order; string value / unknown key / `null` in settings do not crash; all-disabled-by-hand returns all six; settings file contains `{"providers": {"disabled": ["vultr"]}}` after disabling vultr. CLI via `CliRunner`: `providers` lists six rows with enabled state; `providers disable oci` then `providers` shows oci disabled; `status` with no args omits oci while `status -p oci` still shows it; `providers disable nope` exits 2 with a message; disabling the last one exits 1 with a message.
- [ ] **Step 2: Run to confirm failure:** `uv run pytest tests/test_provider_toggle.py -q`
- [ ] **Step 3: Implement** the four functions above in `providers/__init__.py` (keep a private `_ALL` factory for the canonical list; keys from instances' `.key`). Add `providers_app = typer.Typer()` in `cli.py` mounted as `nimbulo providers` with default callback listing a rich table (Key, Provider, Enabled) and `enable`/`disable` commands taking a key; mirror the style of the existing commands. `status` with no `-p` uses `all_providers()` (already does; confirm).
- [ ] **Step 4: Run:** `uv run pytest -q && uv run ruff check src tests` — all green.
- [ ] **Step 5: Commit** `feat: provider enable/disable core API and CLI`.

### Task 2: GUI Providers dialog (live)

**Files:**
- Modify: `src/nimbulo/ui/gui.py` (MainWindow: tabs built from `all_providers()`)
- Create: `tests/test_gui_provider_toggle.py`

**Interfaces:**
- Consumes: Task 1 API.
- Produces: `MainWindow.open_providers_dialog()` (a modal `QDialog` with one `QCheckBox` per provider from `all_providers(include_disabled=True)`, label = provider label, reflecting current state, re-read from settings each time it opens) and `MainWindow.apply_enabled_providers()` (rebuilds the tab set: adds/removes tabs live keeping canonical order, keeps the selected tab if still enabled else selects the first; keeps `_tabs_by_key` consistent; stops no unrelated worker). A "Providers…" button placed in the existing top/bottom bar (match the existing button styling and the real qt-material theme; must not enlarge the default window).

- [ ] **Step 1: Tests (failing, offscreen Qt)**: dialog lists six checkboxes all checked by default; unchecking one and accepting saves the setting and removes that tab; re-checking restores it in original position; unchecking the selected tab selects another; the last checked box cannot be unchecked (the checkbox is disabled when only one is checked, or the change is refused with a message); disabled-via-file provider shows unchecked.
- [ ] **Step 2: Run to confirm failure.**
- [ ] **Step 3: Implement** with a `QDialog` + `QDialogButtonBox`; call `providers.set_enabled` per changed box; then `apply_enabled_providers()`. Reuse the existing tab-creation code path (extract it into a helper; do not duplicate).
- [ ] **Step 4: Run** full suite + ruff.
- [ ] **Step 5: Commit** `feat(gui): Providers dialog with live tab add/remove`.

### Task 3: TUI Providers modal (live)

**Files:**
- Modify: `src/nimbulo/ui/tui.py`
- Create: `tests/test_tui_provider_toggle.py`

**Interfaces:**
- Consumes: Task 1 API.
- Produces: binding `("p", "providers", "Providers")` opening a `ModalScreen` with one `Checkbox` per provider (state re-read on open), Save/Cancel; on Save calls `providers.set_enabled` per change then adds/removes `TabPane`s live (`TabbedContent.add_pane` / `remove_pane`), keeping canonical order and `self._panes` consistent; if the active tab is removed, activate the first remaining. The last enabled checkbox cannot be unchecked. Must fit 80x24.

- [ ] **Step 1: Tests (failing, Textual `run_test` pilot, same style as `tests/test_tui_oci.py`)**: press `p` opens the modal with six checkboxes; toggling one and saving removes its tab and persists; re-enabling restores it in order; disabling the active tab activates another; last-one guard; modal fits 80x24.
- [ ] **Step 2: Run to confirm failure.**
- [ ] **Step 3: Implement.** `compose` must use `all_providers()` (already does) and tolerate the all-disabled fallback.
- [ ] **Step 4: Run** full suite + ruff.
- [ ] **Step 5: Commit** `feat(tui): Providers modal with live tab add/remove`.

### Task 4: Menubar Providers submenu (live) + docs and release notes

**Files:**
- Modify: `src/nimbulo/ui/menubar.py`
- Modify: `README.md`, `CHANGELOG.md`
- Create: `tests/test_menubar_provider_toggle.py`

**Interfaces:**
- Consumes: Task 1 API.
- Produces: a "Providers" submenu of checkable `QAction`s (one per provider) in the tray menu; toggling calls `providers.set_enabled`, then rebuilds the provider entries and controllers live (keep one controller per enabled provider; start/stop only the affected ones); the tray state/tooltip counts only enabled providers; last-one guard (action disabled).
- Docs: README section "Enabling and disabling providers" (settings key, CLI commands, GUI/TUI/menubar controls, note that explicit commands still work); CHANGELOG entry under a new `## [0.3.0]` heading as `### Added`; bump `pyproject.toml` version to `0.3.0` and `uv lock`.

- [ ] **Step 1: Tests (failing, offscreen, same style as `tests/test_menubar_oci.py`)**: submenu has six checked actions; unchecking removes that provider's menu entry and excludes it from the tooltip summary; re-check restores; last-one guard.
- [ ] **Step 2: Run to confirm failure.**
- [ ] **Step 3: Implement**, then README/CHANGELOG/version.
- [ ] **Step 4: Run** full suite + ruff, `uv build`, `uvx twine check dist/*`, clean-venv `nimbulo providers` smoke test.
- [ ] **Step 5: Commit** `feat(menubar): Providers submenu; docs; release 0.3.0`.
