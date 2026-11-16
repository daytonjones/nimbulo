"""Qt tests for the Providers dialog (live tab add/remove). Offscreen; no real CLI/network."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cloud_login import providers, settings  # noqa: E402
from cloud_login.models import AuthState, ProviderStatus  # noqa: E402
from cloud_login.ui import gui  # noqa: E402

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


_WINS: list = []


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for w in _WINS:
        for t in w.findChildren(QThread):
            t.quit()
            t.wait(5000)
    QApplication.processEvents()
    _WINS.clear()


def pump(cond=lambda: False, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)


def make_win():
    w = gui.MainWindow()
    _WINS.append(w)
    pump(lambda: all(not t._threads for t in w._tabs_by_key.values()))
    return w


def tab_keys(w):
    by = {id(t): k for k, t in w._tabs_by_key.items()}
    return [by[id(w.tabs.widget(i))] for i in range(w.tabs.count())]


def apply(w, **states):
    dlg = w._build_providers_dialog()
    for k, v in states.items():
        dlg.provider_boxes[k].setChecked(v)
    w._apply_providers_dialog(dlg)


def test_dialog_lists_all_checked_by_default():
    w = make_win()
    dlg = w._build_providers_dialog()
    assert list(dlg.provider_boxes) == KEYS
    assert all(cb.isChecked() for cb in dlg.provider_boxes.values())
    assert [cb.text() for cb in dlg.provider_boxes.values()] == [
        p.label for p in providers.all_providers(include_disabled=True)
    ]


def test_uncheck_removes_tab_and_saves():
    w = make_win()
    apply(w, gcp=False)
    assert "gcp" not in tab_keys(w) and "gcp" not in w._tabs_by_key
    assert providers.disabled_keys() == ["gcp"]


def test_recheck_restores_original_position():
    w = make_win()
    apply(w, gcp=False)
    apply(w, gcp=True)
    assert tab_keys(w) == KEYS
    assert providers.disabled_keys() == []


def test_selected_tab_preserved_and_fallback():
    w = make_win()
    w.tabs.setCurrentWidget(w._tabs_by_key["oci"])
    apply(w, gcp=False)
    assert w.tabs.currentWidget() is w._tabs_by_key["oci"]
    apply(w, oci=False)
    assert w.tabs.currentIndex() == 0


def test_unrelated_tabs_not_recreated():
    w = make_win()
    before = dict(w._tabs_by_key)
    apply(w, gcp=False)
    assert all(w._tabs_by_key[k] is before[k] for k in w._tabs_by_key)


def test_last_checked_box_disabled():
    w = make_win()
    dlg = w._build_providers_dialog()
    for k in KEYS[1:]:
        dlg.provider_boxes[k].setChecked(False)
    assert not dlg.provider_boxes["aws"].isEnabled()
    dlg.provider_boxes["gcp"].setChecked(True)
    assert dlg.provider_boxes["aws"].isEnabled()


def test_disabled_via_settings_shows_unchecked():
    settings.set("providers.disabled", ["vultr"])
    w = make_win()
    assert "vultr" not in tab_keys(w)
    dlg = w._build_providers_dialog()
    assert not dlg.provider_boxes["vultr"].isChecked()
    assert dlg.provider_boxes["aws"].isChecked()


def test_dialog_rereads_settings_each_open():
    w = make_win()
    assert w._build_providers_dialog().provider_boxes["gcp"].isChecked()
    providers.set_enabled("gcp", False)  # e.g. changed via CLI while GUI runs
    assert not w._build_providers_dialog().provider_boxes["gcp"].isChecked()


def test_removing_tab_with_inflight_status_does_not_crash(monkeypatch):
    from cloud_login.providers.gcp import GCPProvider

    def slow(self, profile=None):
        time.sleep(0.3)
        return ProviderStatus(self.key, AuthState.AUTHENTICATED)

    monkeypatch.setattr(GCPProvider, "status", slow)
    w = gui.MainWindow()
    _WINS.append(w)
    tab = w._tabs_by_key["gcp"]
    assert tab._threads
    apply(w, gcp=False)
    assert tab in w._retired_tabs
    pump(lambda: not w._retired_tabs)
    assert not w._retired_tabs
    pump(timeout=0.3)
    assert "gcp" not in tab_keys(w)


def test_providers_button_exists():
    w = make_win()
    assert w.providers_btn.text().startswith("Providers")


def test_open_dialog_accept_applies_and_cancel_changes_nothing(monkeypatch):
    from PySide6.QtWidgets import QDialog

    w = make_win()
    state = {"result": QDialog.DialogCode.Rejected}

    def fake_exec(self):
        self.provider_boxes["gcp"].setChecked(False)
        return state["result"]

    monkeypatch.setattr(gui._ProvidersDialog, "exec", fake_exec)
    w.open_providers_dialog()
    assert tab_keys(w) == KEYS and providers.disabled_keys() == []
    state["result"] = QDialog.DialogCode.Accepted
    w.open_providers_dialog()
    assert "gcp" not in tab_keys(w) and providers.disabled_keys() == ["gcp"]


def test_set_enabled_failure_shows_status_message(monkeypatch):
    w = make_win()
    monkeypatch.setattr(providers, "set_enabled", lambda k, on: (False, "nope refused"))
    apply(w, gcp=False)
    assert w.statusBar().currentMessage() == "nope refused"
    assert "gcp" in tab_keys(w)


def test_readded_tab_refreshes_status():
    w = make_win()
    apply(w, gcp=False)
    apply(w, gcp=True)
    tab = w._tabs_by_key["gcp"]
    pump(lambda: not tab._threads)
    idx = w.tabs.indexOf(tab)
    assert w.tabs.tabBar().tabTextColor(idx).name() != "#888888" or tab.badge_label.text() != ""
    assert tab.badge_label.text().lower() == AuthState.UNKNOWN.value.lower()


def test_close_with_retired_tab_inflight_joins_threads(monkeypatch):
    from cloud_login.providers.gcp import GCPProvider

    def slow(self, profile=None):
        time.sleep(0.3)
        return ProviderStatus(self.key, AuthState.AUTHENTICATED)

    monkeypatch.setattr(GCPProvider, "status", slow)
    w = gui.MainWindow()
    _WINS.append(w)
    tab = w._tabs_by_key["gcp"]
    apply(w, gcp=False)
    w.close()
    assert not tab.findChildren(QThread) or all(not t.isRunning() for t in tab.findChildren(QThread))


def test_providers_button_reachable_with_log_collapsed_and_window_fits(qapp):
    from qt_material import apply_stylesheet

    apply_stylesheet(qapp, theme=gui.THEME, extra={"density_scale": "-1"})
    qapp.setStyleSheet(qapp.styleSheet() + "\n" + gui.OVERLAY_QSS)
    try:
        w = make_win()
        w.resize(1040, 760)
        w.show()
        QApplication.processEvents()
        assert w.minimumSizeHint().height() <= 760 and w.minimumSizeHint().width() <= 1040
        w._toggle_log()
        QApplication.processEvents()
        assert w.splitter.sizes()[1] == 0
        assert not w.providers_btn.visibleRegion().isEmpty()
        assert w.rect().contains(w.providers_btn.mapTo(w, w.providers_btn.rect().topLeft()))
        w.hide()
    finally:
        qapp.setStyleSheet("")
