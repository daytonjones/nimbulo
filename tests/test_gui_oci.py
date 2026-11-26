"""Qt tests for the OCI api-key form. No real ~/.oci, CLI, network or settings file is touched."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402

import pytest  # noqa: E402

# PySide6 can be installed but unusable (e.g. no libEGL on a headless Linux box): skip, not error.
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QGroupBox, QLineEdit  # noqa: E402

from nimbulo import settings  # noqa: E402
from nimbulo.models import AuthState, Identity, LoginResult, ProviderStatus  # noqa: E402
from nimbulo.providers import oci_config  # noqa: E402
from nimbulo.providers.aws import AWSProvider  # noqa: E402
from nimbulo.providers.oci import OCIProvider  # noqa: E402
from nimbulo.ui import gui  # noqa: E402

USER = "ocid1.user.oc1..aaaaaaaabbbbbbbbcccccccc"
TENANCY = "ocid1.tenancy.oc1..aaaaaaaabbbbbbbbcccccccc"
PEM = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg\n-----END PUBLIC KEY-----"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, qapp, fake_run):
    # never the real ~/.oci/config or oci CLI, even from background status/compartment workers
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", tmp_path / "oci-config")
    monkeypatch.setattr(OCIProvider, "list_secondary", lambda self, profile=None: [])
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(
        OCIProvider,
        "api_key_defaults",
        lambda self: {"profile": "my-profile", "region": "eu-frankfurt-1", "key_dir": str(tmp_path / "keys")},
    )
    for cls in (OCIProvider, AWSProvider):
        monkeypatch.setattr(
            cls,
            "status",
            lambda self, profile=None: ProviderStatus(self.key, AuthState.UNAUTHENTICATED),
        )
    calls: list[dict] = []

    def fake_login(self, method="session", on_output=None, **kwargs):
        calls.append({"method": method, **kwargs})
        if on_output:
            on_output("streamed line")
        return LoginResult(ok=True, message="done <i>", output=PEM + "\nupload steps")

    monkeypatch.setattr(OCIProvider, "login", fake_login)
    return calls


def settle(tab, timeout=5.0):
    """Pump the event loop until every worker thread has finished."""
    app = QApplication.instance()
    end = time.monotonic() + timeout
    quiet = 0
    while time.monotonic() < end:
        app.processEvents()
        quiet = quiet + 1 if not tab._threads else 0
        if quiet >= 5:
            return
        time.sleep(0.01)
    raise AssertionError("worker threads did not finish")


_TABS: list = []


@pytest.fixture(autouse=True)
def join_tab_threads():
    """Join every tab's QThreads before the tab can be garbage-collected.

    ``settle`` returns once ``finished`` has been emitted, but the OS thread may still be
    unwinding; destroying the tab (the threads' parent) then aborts the process.
    """
    yield
    for tab in _TABS:
        for t in tab.findChildren(QThread):
            t.quit()
            t.wait(5000)
    QApplication.processEvents()
    _TABS.clear()


def make_tab(provider=None):
    tab = gui.ProviderTab(provider or OCIProvider())
    _TABS.append(tab)
    settle(tab)
    return tab


def select_method(tab, method):
    tab.method_combo.setCurrentText(method)


def fill(tab, **kw):
    f = tab.oci_fields
    f["profile"].setText(kw.get("profile", "work"))
    f["user"].setText(kw.get("user", USER))
    f["tenancy"].setText(kw.get("tenancy", TENANCY))
    f["region"].setCurrentText(kw.get("region", "us-ashburn-1"))
    f["key_file"].setText(kw.get("key_file", ""))
    f["generate"].setChecked(kw.get("generate", True))
    f["overwrite"].setChecked(kw.get("overwrite", False))


def test_gui_oci_form_hidden_for_other_methods():
    tab = make_tab()
    tab.show()
    assert tab.oci_form.isHidden()  # initial method is session
    select_method(tab, "api-key")
    assert not tab.oci_form.isHidden()
    select_method(tab, "profile")
    assert tab.oci_form.isHidden()
    select_method(tab, "session")
    assert tab.oci_form.isHidden()


def test_gui_oci_form_visible_for_api_key():
    tab = make_tab()
    select_method(tab, "api-key")
    f = tab.oci_fields
    assert f["profile"].text() == "my-profile"
    assert f["region"].currentText() == "eu-frankfurt-1"
    assert f["key_file"].isEnabled()
    f["generate"].setChecked(True)
    assert not f["key_file"].isEnabled() and not tab.oci_browse_btn.isEnabled()
    f["generate"].setChecked(False)
    assert f["key_file"].isEnabled() and tab.oci_browse_btn.isEnabled()
    # nothing secret is requested
    assert all(e.echoMode() == QLineEdit.EchoMode.Normal for e in tab.oci_form.findChildren(QLineEdit))
    assert tab.copy_pubkey_btn.isHidden()
    assert tab.oci_msg.textFormat() == gui.Qt.TextFormat.PlainText


def test_gui_oci_form_invalid_ocid_shows_message_without_calling_login(isolated):
    tab = make_tab()
    select_method(tab, "api-key")
    fill(tab, user="not-an-ocid")
    tab.login_btn.click()
    assert "user OCID" in tab.oci_msg.text()
    fill(tab, region="eu-frankfurt-[1]")
    tab.login_btn.click()
    assert "unknown region" in tab.oci_msg.text() and "eu-frankfurt-1" in tab.oci_msg.text()
    fill(tab, generate=False, key_file="")
    tab.login_btn.click()
    assert "key file" in tab.oci_msg.text()
    fill(tab, profile="bad name/..")
    tab.login_btn.click()
    assert "profile name" in tab.oci_msg.text()
    settle(tab)
    assert isolated == []


def test_gui_oci_form_submit_calls_login_with_kwargs(isolated):
    tab = make_tab()
    logs: list[str] = []
    tab.log_message.connect(logs.append)
    select_method(tab, "api-key")
    fill(tab, generate=True, overwrite=True, key_file="/ignored.pem")
    tab.login_btn.click()
    settle(tab)
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
    text = "\n".join(logs)
    assert "streamed line" in text and "upload steps" in text and "BEGIN PUBLIC KEY" in text
    assert tab.oci_fields["profile"].text() == "work"  # values kept
    assert not tab.copy_pubkey_btn.isHidden()
    assert "upload steps" in tab.oci_hint.text()
    assert tab.login_btn.isEnabled()
    tab.copy_pubkey_btn.click()
    assert QApplication.clipboard().text() == PEM


def test_gui_oci_form_existing_key_file_and_enter_submits(isolated):
    tab = make_tab()
    select_method(tab, "api-key")
    fill(tab, generate=False, key_file="/k/key.pem")
    tab.oci_fields["key_file"].returnPressed.emit()
    settle(tab)
    assert isolated and isolated[0]["key_file"] == "/k/key.pem" and isolated[0]["generate_key"] is False


def test_gui_oci_browse_sets_key_file(monkeypatch):
    tab = make_tab()
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *a, **k: ("/picked/k.pem", ""))
    tab.oci_browse_btn.click()
    assert tab.oci_fields["key_file"].text() == "/picked/k.pem"


def test_gui_oci_session_method_uses_existing_login(isolated):
    tab = make_tab()
    tab.profile_combo.addItem("PROF")
    tab.login_btn.click()
    settle(tab)
    assert isolated == [{"method": "session", "profile": "PROF"}]


def test_gui_oci_status_is_escaped_other_providers_unchanged():
    st = dict(identity=Identity(user="<b>u</b>", account="<i>t</i>"), message="<b>x</b>", active_profile="a<b")
    oci = make_tab()
    oci._on_status_done(ProviderStatus("oci", AuthState.ERROR, **st))
    txt = oci.detail_label.text()
    assert "&lt;b&gt;x&lt;/b&gt;" in txt and "&lt;b&gt;u&lt;/b&gt;" in txt and "<b>x</b>" not in txt
    aws = make_tab(AWSProvider())
    aws._on_status_done(ProviderStatus("aws", AuthState.ERROR, **st))
    # every provider escapes provider-derived text (security fix)
    assert "&lt;b&gt;x&lt;/b&gt;" in aws.detail_label.text() and "<b>x</b>" not in aws.detail_label.text()


def test_gui_other_providers_unchanged(monkeypatch):
    aws = make_tab(AWSProvider())
    assert aws.oci_form is None and aws.copy_pubkey_btn is None
    assert aws.token_edit is not None
    assert [g.title() for g in aws.findChildren(QGroupBox)] == ["Identity", "Credentials"]
    assert not any(c.text().startswith("Generate") for c in aws.findChildren(QCheckBox))
    assert aws.method_combo.itemText(0) == AWSProvider().login_methods()[0]
    seen: list[dict] = []
    monkeypatch.setattr(
        AWSProvider,
        "login",
        lambda self, method="sso", on_output=None, **kw: (
            seen.append({"method": method, **kw}) or LoginResult(ok=True, message="ok")
        ),
    )
    aws.profile_combo.addItem("dev")
    aws.method_combo.setCurrentText("sso")
    aws.login_btn.click()
    settle(aws)
    assert seen == [{"method": "sso", "profile": "dev"}]


@pytest.fixture
def themed(qapp, monkeypatch):
    """Apply the real qt-material theme + OVERLAY_QSS exactly as run() does, then undo."""
    from qt_material import apply_stylesheet

    from nimbulo import providers

    for cls in {type(p) for p in providers.all_providers()}:
        monkeypatch.setattr(cls, "status", lambda self, profile=None: ProviderStatus(self.key, AuthState.UNKNOWN))
        monkeypatch.setattr(cls, "list_secondary", lambda self: [])
    apply_stylesheet(qapp, theme=gui.THEME, extra={"density_scale": "-1"})
    qapp.setStyleSheet(qapp.styleSheet() + "\n" + gui.OVERLAY_QSS)
    yield
    qapp.setStyleSheet("")


def test_gui_oci_tab_fits_default_window(themed):
    from nimbulo import providers

    tabs = {}
    for p in providers.all_providers():
        tabs[p.key] = make_tab(p)
    oci = tabs["oci"]
    select_method(oci, "api-key")
    others = max(t.minimumSizeHint().height() for k, t in tabs.items() if k != "oci")
    # reviewer measured 749 vs 454 before the form moved into a bounded scroll area
    assert oci.minimumSizeHint().height() <= others, (oci.minimumSizeHint().height(), others)
    # at the minimum window size (820x600) the tab area is ~480px high: Login stays visible
    oci.resize(820, 480)
    oci.show()
    QApplication.processEvents()
    assert oci.rect().contains(oci.login_btn.geometry()), (oci.login_btn.geometry(), oci.rect())
    assert oci.oci_scroll.height() >= 40
    assert oci.oci_scroll.horizontalScrollBar().maximum() == 0
    oci.hide()


# --- final review fix wave ----------------------------------------------------

from nimbulo.providers.oci import ConfirmationRequired  # noqa: E402

Yes = gui.QMessageBox.StandardButton.Yes
No = gui.QMessageBox.StandardButton.No


@pytest.fixture
def dialogs(monkeypatch):
    """Patch QMessageBox.question; set ``dialogs.answer``; ``dialogs.seen`` records (text, default)."""

    class D:
        answer = No
        seen: list = []

    d = D()
    d.seen = []

    def question(parent, title, text, buttons=None, default=None):
        d.seen.append((text, default))
        return d.answer

    monkeypatch.setattr(gui.QMessageBox, "question", question)
    return d


def _confirming_login(monkeypatch):
    calls: list[dict] = []

    def fake_login(self, method="session", on_output=None, **kw):
        calls.append({"method": method, **kw})
        if not kw.get("overwrite"):
            return ConfirmationRequired(ok=False, message="profile PROD is an API-key profile")
        return LoginResult(ok=True, message="session login complete")

    monkeypatch.setattr(OCIProvider, "login", fake_login)
    return calls


@pytest.mark.parametrize("answer, retried", [(Yes, True), (No, False)])
def test_gui_session_login_confirm_retries_with_overwrite(monkeypatch, dialogs, answer, retried):
    calls = _confirming_login(monkeypatch)
    dialogs.answer = answer
    tab = make_tab()
    tab.profile_combo.addItem("PROD")
    tab.login_btn.click()
    settle(tab)
    assert len(dialogs.seen) == 1
    text, default = dialogs.seen[0]
    assert "API-key profile" in text and default == No
    expected = [{"method": "session", "profile": "PROD"}]
    if retried:
        expected.append({"method": "session", "profile": "PROD", "overwrite": True})
    assert calls == expected
    assert tab.login_btn.isEnabled()


@pytest.mark.parametrize("answer, confirmed", [(Yes, True), (No, False)])
def test_gui_oci_logout_confirm(monkeypatch, dialogs, answer, confirmed):
    calls: list[tuple] = []

    def fake_logout(self, profile=None, *, confirm=False):
        calls.append((profile, confirm))
        if not confirm:
            return ConfirmationRequired(ok=False, message="profile PROD will be REMOVED from ~/.oci/config")
        return LoginResult(ok=True, message="profile PROD was removed")

    monkeypatch.setattr(OCIProvider, "logout", fake_logout)
    dialogs.answer = answer
    tab = make_tab()
    tab.profile_combo.addItem("PROD")
    tab.logout_btn.click()
    settle(tab)
    assert len(dialogs.seen) == 1 and "REMOVED" in dialogs.seen[0][0] and dialogs.seen[0][1] == No
    assert calls == ([("PROD", False), ("PROD", True)] if confirmed else [("PROD", False)])
    assert tab.logout_btn.isEnabled()


def test_gui_oci_logout_api_key_profile_no_dialog(monkeypatch, dialogs):
    monkeypatch.setattr(
        OCIProvider, "logout", lambda self, profile=None, *, confirm=False: LoginResult(ok=False, message="API key")
    )
    tab = make_tab()
    tab.logout_btn.click()
    settle(tab)
    assert dialogs.seen == []


def test_gui_oci_status_labels_tenancy():
    tab = make_tab()
    tab._on_status_done(ProviderStatus("oci", AuthState.AUTHENTICATED, identity=Identity(account="ocid1.tenancy.x")))
    settle(tab)  # AUTHENTICATED starts a (stubbed) compartment refresh
    txt = tab.detail_label.text()
    assert "<b>Tenancy:</b> ocid1.tenancy.x" in txt and "Compartment:" not in txt


def test_gui_copy_button_reset_on_new_attempt_and_method_switch(isolated):
    tab = make_tab()
    select_method(tab, "api-key")
    fill(tab)
    tab.login_btn.click()
    settle(tab)
    assert not tab.copy_pubkey_btn.isHidden() and tab._pubkey_pem
    select_method(tab, "session")
    assert tab.copy_pubkey_btn.isHidden() and tab._pubkey_pem is None and tab.oci_hint.text() == ""
    select_method(tab, "api-key")
    fill(tab)
    tab.login_btn.click()
    settle(tab)
    assert tab._pubkey_pem
    fill(tab, user="bad")  # a new attempt (even a rejected one) clears the previous key
    tab.login_btn.click()
    assert tab.copy_pubkey_btn.isHidden() and tab._pubkey_pem is None and tab.oci_hint.isHidden()


def test_gui_region_case_is_normalised(isolated):
    tab = make_tab()
    select_method(tab, "api-key")
    fill(tab, region=" US-Ashburn-1 ")
    tab.login_btn.click()
    settle(tab)
    assert isolated and isolated[0]["region"] == "us-ashburn-1"
