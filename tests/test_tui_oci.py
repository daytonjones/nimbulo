"""Textual pilot tests for the OCI api-key form. No real ~/.oci, CLI or network is touched."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Checkbox, Input, Log, Select, Static, TabbedContent

from nimbulo import settings
from nimbulo.models import LoginResult
from nimbulo.providers import oci_config
from nimbulo.providers.oci import OCIProvider
from nimbulo.ui import tui

USER = "ocid1.user.oc1..aaaaaaaabbbbbbbbcccccccc"
TENANCY = "ocid1.tenancy.oc1..aaaaaaaabbbbbbbbcccccccc"
FORM_IDS = ["oci-profile", "oci-user", "oci-tenancy", "oci-region", "oci-keyfile", "oci-generate", "oci-overwrite"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, fake_run):
    # never the real ~/.oci/config, oci CLI or settings file
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", tmp_path / "oci-config")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(tui.ProviderPane, "refresh_status", lambda self: None)
    monkeypatch.setattr(
        OCIProvider,
        "api_key_defaults",
        lambda self: {"profile": "my-profile", "region": "eu-frankfurt-1", "key_dir": "/nonexistent/keys"},
    )
    calls: list[dict] = []

    def fake_login(self, method="session", on_output=None, **kwargs):
        calls.append({"method": method, **kwargs})
        if on_output:
            on_output("streamed line")
        return LoginResult(ok=True, message="done [bold]", output="PUBLIC KEY [red]x[/]\nupload steps")

    monkeypatch.setattr(OCIProvider, "login", fake_login)
    return calls


def run(coro):
    return asyncio.run(coro)


def form(app):
    return app.query_one("#oci-form")


async def click_login(pilot):
    await pilot.click("#login-oci")
    await pilot.pause(0.4)  # Button ignores presses while its "active" effect runs


async def select_method(app, pilot, method):
    app.query_one(TabbedContent).active = "oci"
    await pilot.pause()
    app.query_one("#method-oci", Select).value = method
    await pilot.pause()


def test_oci_form_hidden_for_other_methods():
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            assert form(app).display is False  # initial method is session
            await select_method(app, pilot, "api-key")
            assert form(app).display is True
            await select_method(app, pilot, "profile")
            assert form(app).display is False
            await select_method(app, pilot, "session")
            assert form(app).display is False

    run(go())


def test_oci_form_visible_for_api_key():
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await select_method(app, pilot, "api-key")
            for wid in FORM_IDS:
                app.query_one(f"#{wid}")
            assert app.query_one("#oci-profile", Input).value == "my-profile"
            assert app.query_one("#oci-region", Input).value == "eu-frankfurt-1"
            assert app.query_one("#oci-user", Input).password is False
            # nothing secret-bearing in the form
            assert not any(i.password for i in form(app).query(Input))

    run(go())


def test_invalid_ocid_shows_message_without_calling_login(isolated):
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await select_method(app, pilot, "api-key")
            app.query_one("#oci-user", Input).value = "not-an-ocid"
            app.query_one("#oci-tenancy", Input).value = TENANCY
            app.query_one("#oci-generate", Checkbox).value = True
            await pilot.pause()
            await click_login(pilot)
            msg = str(app.query_one("#oci-msg", Static).render())
            assert "user OCID" in msg
            # bad region suggests the closest, brackets in values stay literal
            app.query_one("#oci-user", Input).value = USER
            app.query_one("#oci-region", Input).value = "eu-frankfurt-[1]"
            await click_login(pilot)
            msg = str(app.query_one("#oci-msg", Static).render())
            assert "unknown region" in msg and "eu-frankfurt-1" in msg and "eu-frankfurt-[1]" in msg
            # neither / both of key file and generate
            app.query_one("#oci-region", Input).value = "eu-frankfurt-1"
            app.query_one("#oci-generate", Checkbox).value = False
            await click_login(pilot)
            assert "key file" in str(app.query_one("#oci-msg", Static).render())
            # "both" is unreachable from the UI (generate disables the key-file input); the
            # validator itself still rejects it
            assert "exactly one" in tui.validate_oci_form(
                {
                    "profile": "p",
                    "user": USER,
                    "tenancy": TENANCY,
                    "region": "us-ashburn-1",
                    "key_file": "/k.pem",
                    "generate_key": True,
                    "overwrite": False,
                }
            )
            # bad profile name
            app.query_one("#oci-generate", Checkbox).value = False
            app.query_one("#oci-profile", Input).value = "bad name/.."
            await click_login(pilot)
            assert "profile name" in str(app.query_one("#oci-msg", Static).render())

    run(go())
    assert isolated == []


def test_submit_calls_login_with_kwargs(isolated):
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await select_method(app, pilot, "api-key")
            app.query_one("#oci-profile", Input).value = "work"
            app.query_one("#oci-user", Input).value = USER
            app.query_one("#oci-tenancy", Input).value = TENANCY
            app.query_one("#oci-region", Input).value = "us-ashburn-1"
            app.query_one("#oci-generate", Checkbox).value = True
            app.query_one("#oci-overwrite", Checkbox).value = True
            await pilot.pause()
            assert app.query_one("#oci-keyfile", Input).disabled is True
            await pilot.click("#login-oci")
            await app.workers.wait_for_complete()
            await pilot.pause()
            lines = "\n".join(app.query_one("#log", Log).lines)
            assert "streamed line" in lines
            assert "PUBLIC KEY [red]x[/]" in lines and "upload steps" in lines
            assert app.query_one("#oci-profile", Input).value == "work"  # values kept

    run(go())
    assert isolated == [
        {
            "method": "api-key",
            "profile": "work",
            "user": USER,
            "tenancy": TENANCY,
            "region": "us-ashburn-1",
            "key_file": None,
            "generate_key": True,
            "overwrite": True,
        }
    ]


def test_enter_in_last_field_submits(isolated):
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await select_method(app, pilot, "api-key")
            app.query_one("#oci-user", Input).value = USER
            app.query_one("#oci-tenancy", Input).value = TENANCY
            kf = app.query_one("#oci-keyfile", Input)
            kf.value = "/k/key.pem"
            kf.focus()
            await pilot.press("enter")
            await app.workers.wait_for_complete()

    run(go())
    assert isolated and isolated[0]["key_file"] == "/k/key.pem" and isolated[0]["generate_key"] is False


def test_session_method_uses_existing_login(isolated):
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            app.query_one(TabbedContent).active = "oci"
            await pilot.pause(0.3)
            app.query_one("#login-oci").press()
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()

    run(go())
    assert isolated and isolated[0]["method"] == "session" and "user" not in isolated[0]


def test_markup_in_status_does_not_crash():
    async def go():
        from nimbulo.models import AuthState, ProviderStatus

        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            pane = app._panes["oci"]
            pane.render_status(ProviderStatus("oci", AuthState.ERROR, message="bad [/oops] [x", active_profile="a[b"))
            await pilot.pause()

    run(go())


def test_other_providers_panes_unchanged():
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            for key in ("aws", "vultr", "digitalocean"):
                assert app.query_one(f"#token-{key}", Input).password is True
            for key, pane in app._panes.items():
                if key != "oci":
                    assert not pane.query("#oci-form")
                    assert not pane.query("#oci-user")
            assert len(app.query("#oci-form")) == 1

    run(go())


def test_form_usable_at_80x24():
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            await select_method(app, pilot, "api-key")
            await pilot.pause(0.3)
            pane = app._panes["oci"]
            assert pane.styles.overflow_y == "auto"
            login = app.query_one("#login-oci")
            login.focus()
            await pilot.pause(0.3)
            assert app.focused is login
            assert login.region.height > 0
            assert app.screen.region.contains_region(login.region)
            # every form control reachable too, and the log still shows
            for wid in FORM_IDS:
                app.query_one(f"#{wid}").scroll_visible()
            await pilot.pause(0.2)
            log = app.query_one("#log")
            assert app.screen.region.contains_region(log.region)
            # other tabs keep the original log height
            app.set_focus(None)
            app.query_one(TabbedContent).active = "aws"
            await pilot.pause(0.5)
            assert not app.has_class("-oci-compact")
            assert log.outer_size.height == 16

    run(go())


def test_other_panes_layout_unchanged_at_80x24():
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            for key, pane in app._panes.items():
                if key != "oci":
                    assert not pane.has_class("-oci")
                    assert pane.styles.overflow_y != "auto"
            assert app.query_one("#log").outer_size.height == 16

    run(go())


# --- final review fix wave ----------------------------------------------------

from nimbulo.models import AuthState, Identity, ProviderStatus  # noqa: E402
from nimbulo.providers.oci import ConfirmationRequired  # noqa: E402


def log_text(app):
    return "\n".join(app.query_one("#log", Log).lines)


async def pick_profile(app, pilot, name):
    app.query_one(TabbedContent).active = "oci"
    await pilot.pause()
    sel = app.query_one("#profile-oci", Select)
    sel.set_options([(name, name)])
    sel.value = name
    await pilot.pause()


async def press_and_wait(app, pilot, wid):
    app.query_one(wid).press()
    await pilot.pause(0.2)
    await app.workers.wait_for_complete()
    await pilot.pause(0.2)


def test_session_login_does_not_pass_form_region(isolated):
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await pick_profile(app, pilot, "PROD")
            assert app.query_one("#oci-region", Input).value == "eu-frankfurt-1"  # DEFAULT's region
            await press_and_wait(app, pilot, "#login-oci")
            await select_method(app, pilot, "profile")
            await press_and_wait(app, pilot, "#login-oci")

    run(go())
    assert isolated == [{"method": "session", "profile": "PROD"}, {"method": "profile", "profile": "PROD"}]


def test_session_login_api_key_profile_needs_second_press(monkeypatch):
    calls: list[dict] = []

    def fake_login(self, method="session", on_output=None, **kw):
        calls.append({"method": method, **kw})
        if not kw.get("overwrite"):
            return ConfirmationRequired(ok=False, message="profile PROD is an API-key profile [x]")
        return LoginResult(ok=True, message="session login complete")

    monkeypatch.setattr(OCIProvider, "login", fake_login)

    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await pick_profile(app, pilot, "PROD")
            await press_and_wait(app, pilot, "#login-oci")
            text = log_text(app)
            assert "API-key profile [x]" in text and "again to confirm" in text
            await press_and_wait(app, pilot, "#login-oci")
            assert "session login complete" in log_text(app)
            # confirmation is single-use
            await press_and_wait(app, pilot, "#login-oci")

    run(go())
    assert calls == [
        {"method": "session", "profile": "PROD"},
        {"method": "session", "profile": "PROD", "overwrite": True},
        {"method": "session", "profile": "PROD"},
    ]


def test_pending_confirmation_cleared_by_method_change(monkeypatch):
    calls: list[dict] = []

    def fake_login(self, method="session", on_output=None, **kw):
        calls.append({"method": method, **kw})
        return ConfirmationRequired(ok=False, message="needs confirm")

    monkeypatch.setattr(OCIProvider, "login", fake_login)

    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await pick_profile(app, pilot, "PROD")
            await press_and_wait(app, pilot, "#login-oci")
            await select_method(app, pilot, "profile")
            await select_method(app, pilot, "session")
            await press_and_wait(app, pilot, "#login-oci")

    run(go())
    assert all("overwrite" not in c for c in calls) and len(calls) == 2


def test_logout_warns_then_confirms(monkeypatch):
    calls: list[tuple] = []

    def fake_logout(self, profile=None, *, confirm=False):
        calls.append((profile, confirm))
        if not confirm:
            return ConfirmationRequired(ok=False, message="profile PROD will be REMOVED from ~/.oci/config")
        return LoginResult(ok=True, message="profile PROD was removed")

    monkeypatch.setattr(OCIProvider, "logout", fake_logout)

    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await pick_profile(app, pilot, "PROD")
            await press_and_wait(app, pilot, "#logout-oci")
            assert "REMOVED" in log_text(app) and "again to confirm" in log_text(app)
            await press_and_wait(app, pilot, "#logout-oci")
            assert "was removed" in log_text(app)

    run(go())
    assert calls == [("PROD", False), ("PROD", True)]


def test_status_labels_tenancy_for_oci():
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            pane = app._panes["oci"]
            pane.render_status(
                ProviderStatus("oci", AuthState.AUTHENTICATED, identity=Identity(account="ocid1.tenancy.x"))
            )
            await pilot.pause()
            text = str(app.query_one("#status-oci", Static).render())
            assert "tenancy: ocid1.tenancy.x" in text and "compartment:" not in text
            gcp = app._panes["gcp"]
            gcp.render_status(ProviderStatus("gcp", AuthState.AUTHENTICATED, identity=Identity(account="proj")))
            await pilot.pause()
            assert "project: proj" in str(app.query_one("#status-gcp", Static).render())

    run(go())


def test_region_case_is_normalised(isolated):
    async def go():
        app = tui.NimbuloTUI()
        async with app.run_test(size=(120, 60)) as pilot:
            await select_method(app, pilot, "api-key")
            app.query_one("#oci-user", Input).value = USER
            app.query_one("#oci-tenancy", Input).value = TENANCY
            app.query_one("#oci-region", Input).value = " US-Ashburn-1 "
            app.query_one("#oci-generate", Checkbox).value = True
            await pilot.pause()
            await press_and_wait(app, pilot, "#login-oci")

    run(go())
    assert isolated and isolated[0]["region"] == "us-ashburn-1"
