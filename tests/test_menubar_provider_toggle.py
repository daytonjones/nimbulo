"""Menubar Providers submenu: live add/remove of provider entries. Offscreen; no real CLI/network."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if sys.platform != "darwin":
    pytest.skip("menu bar is macOS-only", allow_module_level=True)

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nimbulo import providers, settings  # noqa: E402
from nimbulo.models import AuthState, ProviderStatus  # noqa: E402

KEYS = ["aws", "gcp", "azure", "oci", "vultr", "digitalocean"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def stub_status(monkeypatch, qapp):
    for p in providers.all_providers(include_disabled=True):
        cls = type(p)
        monkeypatch.setattr(cls, "status", lambda self, profile=None: ProviderStatus(self.key, AuthState.UNKNOWN))
        monkeypatch.setattr(cls, "list_secondary", lambda self, profile=None: [])


_APPS: list = []


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for a in _APPS:
        for t in a.findChildren(QThread):
            t.quit()
            t.wait(5000)
        if hasattr(a, "tray"):
            a.tray.hide()
    QApplication.processEvents()
    _APPS.clear()


def make(qapp):
    from nimbulo.ui.menubar import MenuBarApp

    a = MenuBarApp(qapp)
    _APPS.append(a)
    return a


def toggles(a):
    return {act.data(): act for act in a.providers_menu.actions()}


def test_submenu_has_six_checked_actions(qapp):
    a = make(qapp)
    t = toggles(a)
    assert list(t) == KEYS
    assert all(act.isCheckable() and act.isChecked() for act in t.values())
    assert list(a._provider_actions) == KEYS


def test_uncheck_removes_entry_controller_and_tooltip(qapp):
    a = make(qapp)
    for k in KEYS:
        a._on_status(k, ProviderStatus(k, AuthState.AUTHENTICATED))
    assert providers.get("oci").label in a.tray.toolTip()
    toggles(a)["oci"].trigger()
    assert providers.is_enabled("oci") is False
    assert "oci" not in a._provider_actions
    assert "oci" not in a._controllers
    assert a.menu.actions().count(a._provider_actions["aws"]) == 1
    assert providers.get("oci").label not in a.tray.toolTip()
    assert not toggles(a)["oci"].isChecked()
    toggles(a)["oci"].trigger()
    assert list(a._provider_actions) == KEYS
    assert toggles(a)["oci"].isChecked()
    # entry order in the real menu is canonical
    menu_actions = a.menu.actions()
    idx = [menu_actions.index(a._provider_actions[k]) for k in KEYS]
    assert idx == sorted(idx)


def test_last_enabled_guard(qapp):
    a = make(qapp)
    for k in KEYS[1:]:
        toggles(a)[k].trigger()
    t = toggles(a)
    assert list(a._provider_actions) == ["aws"]
    assert not t["aws"].isEnabled()
    assert all(t[k].isEnabled() for k in KEYS[1:])


def test_disabled_stale_state_does_not_affect_tray(qapp):
    a = make(qapp)
    a._on_status("aws", ProviderStatus("aws", AuthState.AUTHENTICATED))
    toggles(a)["oci"].trigger()
    a._states["oci"] = AuthState.ERROR  # stale entry
    a._on_status("aws", ProviderStatus("aws", AuthState.AUTHENTICATED))
    for k in KEYS:
        if k not in ("aws", "oci"):
            a._states[k] = AuthState.AUTHENTICATED
    a._update_tray()
    assert providers.get("oci").label not in a.tray.toolTip()
    from nimbulo.ui.menubar import _status_icon

    assert a.tray.icon().pixmap(22, 22).toImage() == _status_icon(AuthState.AUTHENTICATED).pixmap(22, 22).toImage()


def test_external_change_synced_on_refresh_and_about_to_show(qapp):
    a = make(qapp)
    settings.set("providers.disabled", ["gcp", "vultr"])
    a.menu.aboutToShow.emit()
    assert list(a._provider_actions) == ["aws", "azure", "oci", "digitalocean"]
    assert not toggles(a)["gcp"].isChecked()
    settings.set("providers.disabled", [])
    a.refresh_all()
    assert list(a._provider_actions) == KEYS
    assert list(a._controllers) == KEYS


def test_all_disabled_by_hand_falls_back_to_all(qapp):
    settings.set("providers.disabled", KEYS)
    a = make(qapp)
    assert list(a._provider_actions) == KEYS


def test_toggle_off_from_all_disabled_state_removes_provider(qapp):
    settings.set("providers.disabled", KEYS)
    a = make(qapp)
    assert all(act.isChecked() for act in toggles(a).values())
    toggles(a)["gcp"].trigger()
    assert "gcp" not in a._provider_actions
    assert settings.get("providers.disabled") == ["gcp"]
    assert not toggles(a)["gcp"].isChecked()


def test_no_qt_thread_warnings_on_refresh(qapp):
    from PySide6.QtCore import qInstallMessageHandler

    msgs: list[str] = []
    prev = qInstallMessageHandler(lambda _t, _c, m: msgs.append(m))
    try:
        a = make(qapp)
        for _ in range(3):
            a.refresh_all()
            for c in list(a._controllers.values()):
                c.join()
            QApplication.processEvents()
    finally:
        qInstallMessageHandler(prev)
    assert not [m for m in msgs if "moveToThread" in m or "Cannot move" in m], msgs


def test_toggle_syncs_existing_main_window(qapp):
    a = make(qapp)
    a._open_gui()
    _APPS.append(a._gui_window)
    toggles(a)["azure"].trigger()
    assert "azure" not in a._gui_window._tabs_by_key
    toggles(a)["azure"].trigger()
    assert "azure" in a._gui_window._tabs_by_key


def test_removed_controller_with_running_thread_is_reaped_safely(qapp):
    import threading

    gate = threading.Event()
    cls = type(providers.get("oci"))

    def slow(self, profile=None):
        gate.wait(5)
        return ProviderStatus(self.key, AuthState.UNKNOWN)

    a = make(qapp)
    orig = cls.status
    cls.status = slow
    try:
        a._controllers["oci"].start()
        toggles(a)["oci"].trigger()
        assert "oci" not in a._controllers
        gate.set()
        for _ in range(100):
            QApplication.processEvents()
            QThread.msleep(20)
            if not a._retired:
                break
        assert not a._retired
    finally:
        cls.status = orig
        gate.set()
