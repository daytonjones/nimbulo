"""Textual pilot tests for the Providers modal. No real CLI/network is touched."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Checkbox, TabbedContent

from cloud_login import providers, settings
from cloud_login.ui import tui

ORIG_REFRESH = tui.ProviderPane.refresh_status
KEYS = ["aws", "gcp", "azure", "oci", "vultr", "digitalocean"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, fake_run):
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(tui.ProviderPane, "refresh_status", lambda self: None)


def run(coro):
    return asyncio.run(coro)


def tab_ids(app):
    # order as shown in the tab bar (the content-switcher DOM order is irrelevant)
    from textual.widgets import Tabs

    return [t.id.removeprefix("--content-tab-") for t in app.query_one(Tabs).query("Tab")]


async def open_modal(app, pilot):
    await pilot.press("p")
    await pilot.pause()
    return app.screen


async def set_box(pilot, screen, key, value):
    cb = screen.query_one(f"#prov-{key}", Checkbox)
    cb.value = value
    await pilot.pause()


async def save(pilot):
    await pilot.click("#prov-save")
    await pilot.pause(0.4)


def test_modal_opens_with_six_checkboxes_and_fits():
    async def go():
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await open_modal(app, pilot)
            boxes = screen.query(Checkbox)
            assert len(boxes) == 6
            assert all(b.value for b in boxes)
            for w in list(boxes) + [screen.query_one("#prov-save"), screen.query_one("#prov-cancel")]:
                r = w.region
                assert r.width > 0 and r.height > 0
                assert r.bottom <= 24 and r.right <= 80

    run(go())


def test_disable_removes_tab_and_persists():
    async def go():
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "gcp", False)
            await save(pilot)
            assert tab_ids(app) == [k for k in KEYS if k != "gcp"]
            assert "gcp" not in app._panes
            assert "gcp" in providers.disabled_keys()

    run(go())


def test_reenable_restores_canonical_order():
    async def go():
        settings.set("providers.disabled", ["gcp", "oci"])
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            assert tab_ids(app) == ["aws", "azure", "vultr", "digitalocean"]
            screen = await open_modal(app, pilot)
            assert not screen.query_one("#prov-gcp", Checkbox).value  # re-read on open
            await set_box(pilot, screen, "oci", True)
            await set_box(pilot, screen, "gcp", True)
            await save(pilot)
            assert tab_ids(app) == KEYS
            assert list(app._panes) == KEYS or set(app._panes) == set(KEYS)
            assert providers.disabled_keys() == []

    run(go())


def test_disabling_active_tab_activates_first_remaining():
    async def go():
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(TabbedContent).active = "azure"
            await pilot.pause()
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "azure", False)
            await save(pilot)
            assert app.query_one(TabbedContent).active == "aws"

    run(go())


def test_active_tab_kept_when_other_changes():
    async def go():
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(TabbedContent).active = "azure"
            await pilot.pause()
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "aws", False)
            await save(pilot)
            assert app.query_one(TabbedContent).active == "azure"

    run(go())


def test_last_enabled_cannot_be_unchecked():
    async def go():
        settings.set("providers.disabled", ["aws", "gcp", "azure", "oci", "vultr"])
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await open_modal(app, pilot)
            last = screen.query_one("#prov-digitalocean", Checkbox)
            assert last.value and last.disabled
            await set_box(pilot, screen, "aws", True)
            assert not last.disabled

    run(go())


def test_cancel_and_escape_change_nothing():
    async def go():
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "gcp", False)
            await pilot.click("#prov-cancel")
            await pilot.pause()
            assert tab_ids(app) == KEYS
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "gcp", False)
            await pilot.press("escape")
            await pilot.pause()
            assert tab_ids(app) == KEYS
            assert providers.disabled_keys() == []

    run(go())


def test_save_failure_notifies_and_keeps_state(monkeypatch):
    async def go():
        notes = []
        app = tui.CloudLoginTUI()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append((msg, kw)))
        monkeypatch.setattr(providers, "set_enabled", lambda k, e: (False, "boom"))
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "gcp", False)
            await save(pilot)
            assert tab_ids(app) == KEYS
            assert notes and notes[0][1].get("severity") == "error" and "boom" in notes[0][0]

    run(go())


def test_all_disabled_fallback_composes():
    async def go():
        settings.set("providers.disabled", KEYS)
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)):
            assert tab_ids(app) == KEYS

    run(go())


def test_removing_pane_with_inflight_worker_is_safe(monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()

    def slow_status(self):
        started.set()
        release.wait(5)
        from cloud_login.models import AuthState, ProviderStatus

        return ProviderStatus(state=AuthState.UNAUTHENTICATED)

    monkeypatch.setattr(tui.ProviderPane, "refresh_status", ORIG_REFRESH)
    from cloud_login.providers.gcp import GCPProvider

    monkeypatch.setattr(GCPProvider, "status", slow_status)

    async def go():
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert started.wait(5)
            screen = await open_modal(app, pilot)
            await set_box(pilot, screen, "gcp", False)
            await save(pilot)
            release.set()
            await pilot.pause(0.5)
            assert "gcp" not in tab_ids(app)

    run(go())
