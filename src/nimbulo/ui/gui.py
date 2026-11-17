from __future__ import annotations

import html
import inspect
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QSize, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import providers, settings
from ..models import AuthState, LoginResult, ProviderStatus, Urgency, format_expires
from ..providers import oci_config
from ..providers.base import Provider
from ..providers.oci import ConfirmationRequired
from .oci_form import validate_oci_form


# ---------------------------------------------------------------------------
# Theme + palette
# ---------------------------------------------------------------------------

THEME = "dark_teal.xml"

STATE_COLORS: dict[AuthState, str] = {
    AuthState.AUTHENTICATED: "#26C281",
    AuthState.EXPIRED: "#F1C40F",
    AuthState.UNAUTHENTICATED: "#E74C3C",
    AuthState.NOT_INSTALLED: "#7F8C8D",
    AuthState.ERROR: "#C0392B",
    AuthState.UNKNOWN: "#95A5A6",
}

# Overlay QSS layered on top of qt-material's stylesheet.
OVERLAY_QSS = """
QTabWidget::pane {
    border: none;
    margin-top: 4px;
}
QTabBar::tab {
    padding: 10px 22px;
    min-width: 90px;
    font-size: 13px;
}
QFrame#statusCard {
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
}
QFrame#stateStrip {
    border-radius: 3px;
}
QLabel#statusHeader {
    font-size: 18px;
    font-weight: 600;
}
QLabel#statusBadge {
    font-size: 12px;
    font-weight: 700;
    padding: 2px 10px;
    border-radius: 10px;
}
QLabel#statusDetail {
    color: rgba(255, 255, 255, 0.78);
    font-size: 12.5px;
}
QLabel#sectionLabel {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 1.5px;
    color: rgba(255, 255, 255, 0.55);
    text-transform: uppercase;
}
QGroupBox {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: rgba(255, 255, 255, 0.6);
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 1.5px;
}
QPlainTextEdit#activityLog {
    background-color: rgba(0, 0, 0, 0.30);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 8px;
    color: #D7DEE3;
}
QPushButton {
    padding: 8px 18px;
    border-radius: 6px;
    font-weight: 600;
    text-transform: none;
}
QToolButton#revealToggle {
    padding: 2px 10px;
    margin-left: 6px;
    border-radius: 6px;
}
"""


# ---------------------------------------------------------------------------
# Worker plumbing
# ---------------------------------------------------------------------------


class _Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    line = Signal(str)

    def __init__(self, fn: Callable, args: tuple, kwargs: dict) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            params = inspect.signature(self._fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "on_output" in params and "on_output" not in self._kwargs:
            self._kwargs["on_output"] = self.line.emit
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(repr(exc))
            return
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Visible height of the OCI api-key form's scroll area (the form scrolls inside it).
OCI_FORM_MIN_HEIGHT = 140

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def _provider_icon(key: str, size: int = 22) -> QIcon:
    svg_path = ASSETS_DIR / f"{key}.svg"
    if not svg_path.exists():
        return QIcon()
    renderer = QSvgRenderer(str(svg_path))
    pix = QPixmap(QSize(size, size))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def _provider_icon_with_state(key: str, state: AuthState | None, size: int = 22) -> QIcon:
    """Provider logo + small colored status dot in the bottom-right corner."""
    base = _provider_icon(key, size)
    if state is None:
        return base
    pix = base.pixmap(QSize(size, size))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    dot = max(7, size // 3)
    x = size - dot - 1
    y = size - dot - 1
    # White outline for contrast against the logo
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("white"))
    painter.drawEllipse(x - 1, y - 1, dot + 2, dot + 2)
    painter.setBrush(QColor(STATE_COLORS[state]))
    painter.drawEllipse(x, y, dot, dot)
    painter.end()
    return QIcon(pix)


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionLabel")
    return lbl


# ---------------------------------------------------------------------------
# Per-provider tab
# ---------------------------------------------------------------------------


class ProviderTab(QWidget):
    log_message = Signal(str)
    status_changed = Signal(object)  # carries AuthState
    threads_idle = Signal()  # emitted when the last worker thread finished

    def __init__(self, provider: Provider) -> None:
        super().__init__()
        self.provider = provider
        self._threads: list[QThread] = []
        self._workers: list[_Worker] = []
        # First refresh uses the saved profile from last session, if any.
        self._initial_profile: str | None = settings.get(f"last_profile.{provider.key}")
        self._initial_secondary: str | None = settings.get(f"last_secondary.{provider.key}")
        # OCI-only widgets (None for every other provider)
        self.oci_form: QGroupBox | None = None
        self.oci_scroll: QScrollArea | None = None
        self.oci_fields: dict[str, Any] = {}
        self.oci_browse_btn: QPushButton | None = None
        self.oci_msg: QLabel | None = None
        self.oci_hint: QLabel | None = None
        self.copy_pubkey_btn: QPushButton | None = None
        self._pubkey_pem: str | None = None
        self._oci_login_kwargs: dict[str, Any] = {}  # last OCI login, for a confirmed retry
        self._oci_logout_profile: str | None = None  # last OCI logout target, for a confirmed retry
        self._build()
        self.refresh_status()

    # --- layout -----------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(16)
        if self.provider.key == "oci":
            # tighter than the other tabs so the API-key form fits in the same height
            outer.setContentsMargins(20, 10, 20, 10)
            outer.setSpacing(8)

        # --- status card ---
        self.status_card = QFrame()
        self.status_card.setObjectName("statusCard")
        card = QHBoxLayout(self.status_card)
        card.setContentsMargins(12, 12, 14, 12)
        card.setSpacing(14)

        self.state_strip = QFrame()
        self.state_strip.setObjectName("stateStrip")
        self.state_strip.setFixedWidth(6)
        self.state_strip.setMinimumHeight(60)
        self._set_strip(AuthState.UNKNOWN)
        card.addWidget(self.state_strip)

        card_text = QVBoxLayout()
        card_text.setContentsMargins(0, 14, 0, 14)
        card_text.setSpacing(4)

        header_row = QHBoxLayout()
        self.header_label = QLabel(self.provider.label)
        self.header_label.setObjectName("statusHeader")
        header_row.addWidget(self.header_label)
        header_row.addSpacing(10)
        self.badge_label = QLabel("…")
        self.badge_label.setObjectName("statusBadge")
        self._set_badge(AuthState.UNKNOWN, "loading")
        header_row.addWidget(self.badge_label)
        header_row.addStretch()
        card_text.addLayout(header_row)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("statusDetail")
        self.detail_label.setTextFormat(Qt.TextFormat.RichText)
        self.detail_label.setWordWrap(True)
        card_text.addWidget(self.detail_label)
        card.addLayout(card_text)
        outer.addWidget(self.status_card)

        methods = self.provider.login_methods()
        needs_picker_row = self.provider.profile_label is not None or len(methods) > 1

        # --- selectors ---
        self.profile_combo: QComboBox | None = None
        self.method_combo: QComboBox | None = None
        if needs_picker_row:
            picker_box = QGroupBox("Identity")
            picker_layout = QHBoxLayout(picker_box)
            picker_layout.setContentsMargins(14, 14, 14, 12)
            picker_layout.setSpacing(10)
            if self.provider.profile_label is not None:
                col = QVBoxLayout()
                col.setSpacing(4)
                col.addWidget(_section_label(self.provider.profile_label))
                self.profile_combo = QComboBox()
                self.profile_combo.setMinimumWidth(220)
                self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
                col.addWidget(self.profile_combo)
                picker_layout.addLayout(col)
            if len(methods) > 1:
                col = QVBoxLayout()
                col.setSpacing(4)
                col.addWidget(_section_label("Method"))
                self.method_combo = QComboBox()
                for m in methods:
                    self.method_combo.addItem(m)
                self.method_combo.setMinimumWidth(140)
                col.addWidget(self.method_combo)
                picker_layout.addLayout(col)
            picker_layout.addStretch()
            outer.addWidget(picker_box)

        # --- secondary (project / subscription) ---
        self._sec_box: QGroupBox | None = None
        self.secondary_combo: QComboBox | None = None
        self.set_secondary_btn: QPushButton | None = None
        self.refresh_secondary_btn: QPushButton | None = None
        if self.provider.secondary_label is not None:
            sec_box = QGroupBox(self.provider.secondary_label.upper() + "S")
            sec_layout = QHBoxLayout(sec_box)
            sec_layout.setContentsMargins(14, 14, 14, 12)
            sec_layout.setSpacing(10)
            self.secondary_combo = QComboBox()
            self.secondary_combo.setMinimumWidth(360)
            self.secondary_combo.setEditable(True)
            self.secondary_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.secondary_combo.lineEdit().setPlaceholderText(
                f"Type to filter {self.provider.secondary_label.lower()}s…"
            )
            # Built-in completer with substring + case-insensitive matching
            completer = self.secondary_combo.completer()
            if completer is not None:
                completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
                completer.setFilterMode(Qt.MatchFlag.MatchContains)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            sec_layout.addWidget(self.secondary_combo, 1)
            self.set_secondary_btn = QPushButton(f"Set {self.provider.secondary_label}")
            self.set_secondary_btn.clicked.connect(self.do_set_secondary)
            sec_layout.addWidget(self.set_secondary_btn)
            self.refresh_secondary_btn = QPushButton("Refresh")
            self.refresh_secondary_btn.clicked.connect(self.refresh_secondary)
            sec_layout.addWidget(self.refresh_secondary_btn)
            self._sec_box = sec_box
            outer.addWidget(sec_box)

        # --- credentials (token / access key) ---
        self.token_edit: QLineEdit | None = None
        self.reveal_btn: QToolButton | None = None
        if self.provider.key in {"aws", "vultr", "digitalocean"}:
            cred_box = QGroupBox("Credentials")
            cred_layout = QHBoxLayout(cred_box)
            cred_layout.setContentsMargins(14, 14, 14, 12)
            cred_layout.setSpacing(8)
            self.token_edit = QLineEdit()
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.token_edit.setMinimumHeight(28)
            placeholder = {
                "aws": "for `access-key` method: AKID:SECRET   (otherwise leave blank)",
                "vultr": "Vultr API key (stored in macOS Keychain)",
                "digitalocean": "DigitalOcean token (stored in macOS Keychain)",
            }[self.provider.key]
            self.token_edit.setPlaceholderText(placeholder)
            cred_layout.addWidget(self.token_edit, 1)

            self.reveal_btn = QToolButton()
            self.reveal_btn.setObjectName("revealToggle")
            self.reveal_btn.setCheckable(True)
            self.reveal_btn.setText("Show")
            self.reveal_btn.toggled.connect(self._toggle_token_reveal)
            cred_layout.addWidget(self.reveal_btn)
            outer.addWidget(cred_box)

        if self.provider.key == "oci":
            # The form scrolls inside a bounded area so it never grows the tab
            # (the tab must stay no taller than the other providers' tabs).
            self.oci_scroll = QScrollArea()
            self.oci_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.oci_scroll.setWidgetResizable(True)
            self.oci_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.oci_scroll.setMinimumHeight(OCI_FORM_MIN_HEIGHT)
            self.oci_scroll.setWidget(self._build_oci_form())
            outer.addWidget(self.oci_scroll, 1)
            if self.method_combo is not None:
                self.method_combo.currentIndexChanged.connect(self._sync_oci_form)
            self._sync_oci_form()

        # --- action buttons ---
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.clicked.connect(self.refresh_status)
        btn_row.addWidget(self.refresh_btn)

        self.login_btn = QPushButton("🔑  Login / Re-auth")
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self.do_login)
        btn_row.addWidget(self.login_btn)

        if self.provider.key == "oci":
            self.copy_pubkey_btn = QPushButton("Copy public key")
            self.copy_pubkey_btn.clicked.connect(self._copy_pubkey)
            self.copy_pubkey_btn.hide()  # only after a key pair was generated
            btn_row.addWidget(self.copy_pubkey_btn)

        if self.provider.key in {"vultr", "digitalocean"}:
            self.logout_btn = QPushButton("🗑  Forget Key")
        else:
            self.logout_btn = QPushButton("⏻  Logout")
        self.logout_btn.clicked.connect(self.do_logout)
        btn_row.addWidget(self.logout_btn)
        outer.addLayout(btn_row)

        outer.addStretch()

    # --- OCI api-key form (oci only) --------------------------------------

    def _build_oci_form(self) -> QGroupBox:
        try:
            defaults = self.provider.api_key_defaults()
        except Exception:  # noqa: BLE001
            defaults = {}
        box = QGroupBox("API key")
        self.oci_form = box
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 14, 14, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        profile = QLineEdit(defaults.get("profile", ""))
        profile.setPlaceholderText("profile name")
        region = QComboBox()
        region.setEditable(True)
        region.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        region.addItems(list(oci_config.KNOWN_REGIONS))
        region.setCurrentText(defaults.get("region", ""))
        region.lineEdit().setPlaceholderText("region (us-ashburn-1)")
        completer = region.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        user = QLineEdit()
        user.setPlaceholderText("ocid1.user.oc1..…")
        tenancy = QLineEdit()
        tenancy.setPlaceholderText("ocid1.tenancy.oc1..…")
        key_file = QLineEdit()
        key_file.setPlaceholderText("existing private key file (.pem)")
        self.oci_browse_btn = QPushButton("Browse…")
        self.oci_browse_btn.clicked.connect(self._browse_key_file)
        generate = QCheckBox("Generate a new key pair")
        overwrite = QCheckBox("Overwrite existing profile")
        self.oci_fields = {
            "profile": profile, "user": user, "tenancy": tenancy, "region": region,
            "key_file": key_file, "generate": generate, "overwrite": overwrite,
        }
        for w, tip in ((profile, "Profile name"), (region, "Region"), (user, "User OCID"),
                       (tenancy, "Tenancy OCID"), (key_file, "Private key file")):
            w.setToolTip(tip)
        grid.addWidget(profile, 0, 0)
        grid.addWidget(region, 0, 1, 1, 2)
        grid.addWidget(user, 1, 0, 1, 3)
        grid.addWidget(tenancy, 2, 0, 1, 3)
        grid.addWidget(key_file, 3, 0, 1, 2)
        grid.addWidget(self.oci_browse_btn, 3, 2)
        grid.addWidget(generate, 4, 0)
        grid.addWidget(overwrite, 4, 1, 1, 2)
        self.oci_msg = QLabel("")
        self.oci_msg.setTextFormat(Qt.TextFormat.PlainText)
        self.oci_msg.setWordWrap(True)
        self.oci_msg.setStyleSheet("color: #E74C3C;")
        grid.addWidget(self.oci_msg, 5, 0, 1, 3)
        self.oci_hint = QLabel("")
        self.oci_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.oci_hint.setWordWrap(True)
        self.oci_hint.hide()
        grid.addWidget(self.oci_hint, 6, 0, 1, 3)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        generate.toggled.connect(self._oci_generate_toggled)
        # Enter moves to the next field; Enter in the last one submits.
        profile.returnPressed.connect(user.setFocus)
        user.returnPressed.connect(tenancy.setFocus)
        tenancy.returnPressed.connect(region.setFocus)
        region.lineEdit().returnPressed.connect(self._oci_region_enter)
        key_file.returnPressed.connect(self.do_login)
        for a, b in ((profile, region), (region, user), (user, tenancy), (tenancy, generate),
                     (generate, key_file), (key_file, self.oci_browse_btn), (self.oci_browse_btn, overwrite)):
            QWidget.setTabOrder(a, b)
        return box

    def _oci_region_enter(self) -> None:
        if self.oci_fields["generate"].isChecked():
            self.do_login()
        else:
            self.oci_fields["key_file"].setFocus()

    def _oci_generate_toggled(self, checked: bool) -> None:
        self.oci_fields["key_file"].setEnabled(not checked)
        if self.oci_browse_btn is not None:
            self.oci_browse_btn.setEnabled(not checked)

    def _browse_key_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select OCI private key", str(Path.home()), "PEM keys (*.pem);;All files (*)"
        )
        if path:
            self.oci_fields["key_file"].setText(path)

    def _reset_oci_result(self) -> None:
        """Forget the previous api-key result (public key, Copy button, upload hint)."""
        self._pubkey_pem = None
        if self.copy_pubkey_btn is not None:
            self.copy_pubkey_btn.hide()
        if self.oci_hint is not None:
            self.oci_hint.setText("")
            self.oci_hint.hide()

    @Slot()
    def _sync_oci_form(self) -> None:
        self._reset_oci_result()
        if self.oci_form is not None:
            show = self._selected_method() == "api-key"
            self.oci_form.setVisible(show)
            if self._sec_box is not None:
                # compartments are irrelevant while creating a profile; frees height for the form
                self._sec_box.setVisible(not show)
            if self.oci_scroll is not None:
                self.oci_scroll.setVisible(show)

    def oci_form_values(self) -> dict[str, Any]:
        f = self.oci_fields
        generate = f["generate"].isChecked()
        return {
            "profile": f["profile"].text().strip(),
            "user": f["user"].text().strip(),
            "tenancy": f["tenancy"].text().strip(),
            "region": f["region"].currentText().strip().lower(),
            "key_file": None if generate else (f["key_file"].text().strip() or None),
            "generate_key": generate,
            "overwrite": f["overwrite"].isChecked(),
        }

    def _copy_pubkey(self) -> None:
        if self._pubkey_pem:
            QApplication.clipboard().setText(self._pubkey_pem)
            self._log("public key copied to clipboard")

    def _show_oci_result(self, result: LoginResult) -> None:
        """After an api-key login: stream the output to the log, offer copy + instructions."""
        out = result.output or ""
        for line in out.splitlines():
            if line.strip():
                self._log(line)
        m = re.search(r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----", out, re.DOTALL)
        self._pubkey_pem = m.group(0) if m else None
        if self.copy_pubkey_btn is not None:
            self.copy_pubkey_btn.setVisible(self._pubkey_pem is not None)
        if self.oci_hint is not None:
            hint = (out.replace(m.group(0), "") if m else out).strip()
            self.oci_hint.setText(hint)  # PlainText label, no markup interpretation
            self.oci_hint.setVisible(bool(hint))

    # --- threading helper -------------------------------------------------

    def _start(self, fn: Callable, on_done: Callable, *args: Any, **kwargs: Any) -> None:
        thread = QThread(self)
        worker = _Worker(fn, args, kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_done, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        worker.line.connect(self._on_output_line, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._reap_finished, Qt.ConnectionType.QueuedConnection)
        self._threads.append(thread)
        self._workers.append(worker)
        thread.start()

    @Slot()
    def _reap_finished(self) -> None:
        # Runs on the main thread (queued from QThread.finished). The worker is parentless and
        # was moved to the worker thread, so we never touch its thread affinity or call its
        # deleteLater (an event posted to a finished thread never runs, and destroying it on
        # the worker thread deadlocks on the GIL). Instead, once the thread has really
        # stopped, drop our references so Python destroys the worker here on the main thread,
        # then let Qt delete the QThread.
        sender = self.sender()
        for t, w in list(zip(self._threads, self._workers)):
            if t is not sender and t.isRunning():
                continue
            t.wait()
            self._threads.remove(t)
            self._workers.remove(w)
            del w
            t.deleteLater()
        if not self._threads:
            self.threads_idle.emit()

    # --- presentation -----------------------------------------------------

    def _set_badge(self, state: AuthState, text: str | None = None) -> None:
        color = STATE_COLORS[state]
        self.badge_label.setText((text or state.value).upper())
        self.badge_label.setStyleSheet(
            f"QLabel#statusBadge {{ background-color: {color}; color: #0E1116; }}"
        )
        self.status_changed.emit(state)

    def _set_strip(self, state: AuthState) -> None:
        color = STATE_COLORS[state]
        self.state_strip.setStyleSheet(
            f"QFrame#stateStrip {{ background-color: {color}; border-radius: 3px; }}"
        )

    def _toggle_token_reveal(self, checked: bool) -> None:
        if self.token_edit is None or self.reveal_btn is None:
            return
        self.token_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.reveal_btn.setText("Hide" if checked else "Show")

    # --- common slots -----------------------------------------------------

    def _log(self, msg: str) -> None:
        self.log_message.emit(f"[{self.provider.key}] {msg}")

    @Slot(str)
    def _on_output_line(self, line: str) -> None:
        if line.strip():
            self._log(line)

    @Slot(str)
    def _on_failed(self, msg: str) -> None:
        self._log(f"error: {msg}")
        self.refresh_btn.setEnabled(True)
        self.login_btn.setEnabled(True)
        self.logout_btn.setEnabled(True)
        if self.set_secondary_btn is not None:
            self.set_secondary_btn.setEnabled(True)
        if self.refresh_secondary_btn is not None:
            self.refresh_secondary_btn.setEnabled(True)

    # --- status -----------------------------------------------------------

    @Slot()
    def refresh_status(self) -> None:
        self.refresh_btn.setEnabled(False)
        self._set_badge(AuthState.UNKNOWN, "checking")
        self._set_strip(AuthState.UNKNOWN)
        profile: str | None = None
        if self._initial_profile:
            profile = self._initial_profile
            self._initial_profile = None  # only on the first refresh
        elif self.profile_combo is not None:
            v = self.profile_combo.currentText().strip()
            profile = v or None
        self._log(f"refreshing{(' (' + profile + ')') if profile else ''}…")
        self._start(self.provider.status, self._on_status_done, profile=profile)

    @Slot(int)
    def _on_profile_changed(self, _idx: int) -> None:
        # Programmatic combo updates use blockSignals, so this only fires on
        # actual user selection. Trigger a status refresh for the new profile.
        if self.profile_combo is None or self.profile_combo.signalsBlocked():
            return
        v = self.profile_combo.currentText().strip()
        if v:
            settings.set(f"last_profile.{self.provider.key}", v)
        self.refresh_status()

    @Slot(object)
    def _on_status_done(self, s: ProviderStatus) -> None:
        self.refresh_btn.setEnabled(True)
        self._set_badge(s.state)
        self._set_strip(s.state)
        ident = s.identity
        # Provider-derived text (identities, CLI stderr) goes into a RichText label:
        # always escape it so it cannot inject markup.
        esc = html.escape
        details: list[str] = []
        if ident:
            if ident.user:
                details.append(f"<b>User:</b> {esc(ident.user)}")
            if ident.account:
                # OCI's account is the tenancy OCID, not a compartment
                label = "Tenancy" if self.provider.key == "oci" else (self.provider.secondary_label or "Account")
                details.append(f"<b>{label}:</b> {esc(ident.account)}")
            if ident.expires_at:
                exp_label, urg = format_expires(ident.expires_at)
                color = {
                    Urgency.OK: "#26C281",
                    Urgency.SOON: "#F1C40F",
                    Urgency.EXPIRED: "#E74C3C",
                }[urg]
                details.append(
                    f'<b>Expires:</b> <span style="color:{color}; font-weight:600;">{exp_label}</span>'
                )
        if s.active_profile:
            details.append(f"<b>Active:</b> {esc(s.active_profile)}")
        if s.message:
            details.append(f'<span style="color:#8898A4;">{esc(s.message)}</span>')
        self.detail_label.setText("<br/>".join(details) if details else "—")

        if self.profile_combo is not None:
            prev = self.profile_combo.currentText()
            self.profile_combo.blockSignals(True)
            self.profile_combo.clear()
            for p in s.profiles:
                self.profile_combo.addItem(p)
            if s.active_profile and s.active_profile in s.profiles:
                i = self.profile_combo.findText(s.active_profile)
                if i >= 0:
                    self.profile_combo.setCurrentIndex(i)
            elif prev:
                i = self.profile_combo.findText(prev)
                if i >= 0:
                    self.profile_combo.setCurrentIndex(i)
            self.profile_combo.blockSignals(False)

        if self.secondary_combo is not None and s.ok:
            self.refresh_secondary()

    # --- secondary --------------------------------------------------------

    @Slot()
    def refresh_secondary(self) -> None:
        if self.secondary_combo is None:
            return
        self._log(f"listing {self.provider.secondary_label.lower()}s…")
        if self.refresh_secondary_btn is not None:
            self.refresh_secondary_btn.setEnabled(False)
        self._start(self.provider.list_secondary, self._on_secondary_done)

    @Slot(object)
    def _on_secondary_done(self, items: list[tuple[str, str]]) -> None:
        if self.secondary_combo is None:
            return
        if self.refresh_secondary_btn is not None:
            self.refresh_secondary_btn.setEnabled(True)
        prev = (
            self._initial_secondary
            or self.secondary_combo.currentData()
            or self.secondary_combo.currentText()
        )
        self._initial_secondary = None
        self.secondary_combo.blockSignals(True)
        self.secondary_combo.clear()
        for sid, name in items:
            label = f"{name}  —  {sid}" if name and name != sid else sid
            self.secondary_combo.addItem(label, userData=sid)
        if prev:
            for i in range(self.secondary_combo.count()):
                if self.secondary_combo.itemData(i) == prev:
                    self.secondary_combo.setCurrentIndex(i)
                    break
        self.secondary_combo.blockSignals(False)
        self._log(f"{len(items)} {self.provider.secondary_label.lower()}(s) available")

    def do_set_secondary(self) -> None:
        if self.secondary_combo is None:
            return
        sid = self.secondary_combo.currentData() or self.secondary_combo.currentText().split(" ", 1)[0].strip()
        if not sid:
            self._log(f"no {self.provider.secondary_label.lower()} selected")
            return
        if self.set_secondary_btn is not None:
            self.set_secondary_btn.setEnabled(False)
        self._log(f"setting {self.provider.secondary_label.lower()} to {sid}…")
        self._start(self.provider.set_secondary, self._on_set_secondary_done, sid)

    @Slot(object)
    def _on_set_secondary_done(self, result: LoginResult) -> None:
        if self.set_secondary_btn is not None:
            self.set_secondary_btn.setEnabled(True)
        if result.ok and self.secondary_combo is not None:
            sid = self.secondary_combo.currentData() or self.secondary_combo.currentText().split(" ", 1)[0].strip()
            if sid:
                settings.set(f"last_secondary.{self.provider.key}", sid)
        self._log(("OK: " if result.ok else "FAIL: ") + result.message)
        self.refresh_status()

    # --- login / logout ---------------------------------------------------

    def _selected_method(self) -> str:
        if self.method_combo is not None:
            return self.method_combo.currentText()
        return self.provider.login_methods()[0]

    def _selected_profile(self) -> str:
        if self.profile_combo is None:
            return ""
        return self.profile_combo.currentText().strip()

    def do_login(self) -> None:
        method = self._selected_method()
        kwargs: dict[str, Any] = {}
        key = self.provider.key

        if key == "aws":
            if method == "sso":
                profile = self._selected_profile()
                if not profile:
                    self._log("pick a profile first")
                    return
                kwargs["profile"] = profile
            elif method == "access-key":
                raw = (self.token_edit.text() if self.token_edit else "").strip()
                if ":" not in raw:
                    self._log("for access-key, enter `AKID:SECRET` in the token field")
                    return
                akid, secret = raw.split(":", 1)
                kwargs.update(
                    profile=self._selected_profile() or "default",
                    access_key_id=akid.strip(),
                    secret_access_key=secret.strip(),
                )
        elif key == "gcp":
            profile = self._selected_profile()
            if profile:
                kwargs["configuration"] = profile
        elif key == "azure":
            pass
        elif key == "oci":
            self._reset_oci_result()
            if method == "api-key":
                values = self.oci_form_values()
                problem = validate_oci_form(values)
                self.oci_msg.setText(problem or "")
                if problem:
                    return
                kwargs.update(values)
            else:
                profile = self._selected_profile()
                if profile:
                    kwargs["profile"] = profile
        elif key in {"vultr", "digitalocean"}:
            token = (self.token_edit.text() if self.token_edit else "").strip()
            if token:
                kwargs["token"] = token
            # else: provider.login() resolves from env → CLI config file → keyring

        self.login_btn.setEnabled(False)
        self._log(f"login method={method} starting…")
        if key == "oci":
            self._oci_login_kwargs = dict(method=method, **kwargs)
        self._start(self.provider.login, self._on_login_done, method=method, **kwargs)

    @Slot(object)
    def _on_login_done(self, result: LoginResult) -> None:
        self.login_btn.setEnabled(True)
        self._log(("OK: " if result.ok else "FAIL: ") + result.message)
        if self.provider.key == "oci" and isinstance(result, ConfirmationRequired):
            if self._confirm_oci("Convert OCI profile?", result.message):
                self.login_btn.setEnabled(False)
                self._log("confirmed; login starting…")
                self._start(self.provider.login, self._on_login_done, **self._oci_login_kwargs, overwrite=True)
                return
            self._log("login cancelled")
        if self.provider.key == "oci" and self.oci_form is not None and self._selected_method() == "api-key":
            self._show_oci_result(result)
        if self.token_edit is not None:
            self.token_edit.clear()
            if self.reveal_btn is not None and self.reveal_btn.isChecked():
                self.reveal_btn.setChecked(False)
        self.refresh_status()

    def do_logout(self) -> None:
        if self.provider.key in {"vultr", "digitalocean"}:
            reply = QMessageBox.question(
                self,
                f"Forget {self.provider.label} key?",
                (
                    f"This will delete the stored {self.provider.label} API key "
                    "from your macOS Keychain.\n\n"
                    "You'll need to paste it again next time you want to log in.\n\n"
                    "Continue?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._log("forget cancelled")
                return
            self.logout_btn.setEnabled(False)
            self._log("forgetting stored key…")
            self._start(self.provider.forget, self._on_logout_done)
            return
        self.logout_btn.setEnabled(False)
        self._log("logout…")
        if self.provider.key == "oci":
            # the provider refuses first (ConfirmationRequired); _on_logout_done asks the user
            self._oci_logout_profile = self._selected_profile() or None
            self._start(self.provider.logout, self._on_logout_done, self._oci_logout_profile)
            return
        self._start(self.provider.logout, self._on_logout_done)

    def _confirm_oci(self, title: str, message: str) -> bool:
        reply = QMessageBox.question(
            self,
            title,
            message + "\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    @Slot(object)
    def _on_logout_done(self, result: LoginResult) -> None:
        self.logout_btn.setEnabled(True)
        self._log(("OK: " if result.ok else "FAIL: ") + result.message)
        if self.provider.key == "oci" and isinstance(result, ConfirmationRequired):
            if self._confirm_oci("Remove OCI profile?", result.message):
                self.logout_btn.setEnabled(False)
                self._log("confirmed; logout…")
                # same profile the warning named, even if the picker changed meanwhile
                self._start(self.provider.logout, self._on_logout_done, self._oci_logout_profile, confirm=True)
                return
            self._log("logout cancelled")
        self.refresh_status()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class _ProvidersDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.provider_boxes: dict[str, QCheckBox] = {}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("nimbulo")
        geom = settings.get("gui.window_geometry")
        if isinstance(geom, list) and len(geom) == 4:
            self.setGeometry(*geom)
        else:
            self.resize(1040, 760)
        self.setMinimumSize(820, 600)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(22, 22))
        # Always-visible spot (unlike the collapsible log panel).
        self.providers_btn = QPushButton("Providers…")
        self.providers_btn.setFlat(True)
        self.providers_btn.clicked.connect(self.open_providers_dialog)
        self.tabs.setCornerWidget(self.providers_btn, Qt.Corner.TopRightCorner)

        self._tabs_by_key: dict[str, ProviderTab] = {}
        self._retired_tabs: list[ProviderTab] = []
        for p in providers.all_providers():
            self._add_provider_tab(p, self.tabs.count())

        # --- log panel (header + view) lives in its own widget so we can
        # collapse the whole thing via the splitter or the Hide button.
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(6)

        log_header = QHBoxLayout()
        log_title = QLabel("ACTIVITY")
        log_title.setObjectName("sectionLabel")
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.collapse_log_btn = QPushButton("Hide")
        self.collapse_log_btn.setFlat(True)
        self.collapse_log_btn.clicked.connect(self._toggle_log)
        log_header.addWidget(self.collapse_log_btn)
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setFlat(True)
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(self.clear_log_btn)
        log_layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("activityLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        f = QFont("Menlo")
        f.setPointSize(11)
        self.log_view.setFont(f)
        log_layout.addWidget(self.log_view, 1)

        # --- splitter: tabs on top, log on bottom; user can drag to resize.
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setHandleWidth(8)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(log_panel)
        self.splitter.setCollapsible(0, False)  # tabs always visible
        self.splitter.setCollapsible(1, True)   # log can collapse
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        saved_sizes = settings.get("gui.splitter_sizes")
        if isinstance(saved_sizes, list) and len(saved_sizes) == 2 and all(isinstance(x, int) for x in saved_sizes):
            self.splitter.setSizes(saved_sizes)
        else:
            self.splitter.setSizes([560, 180])
        outer.addWidget(self.splitter, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _add_provider_tab(self, p: Provider, index: int) -> ProviderTab:
        tab = ProviderTab(p)
        tab.log_message.connect(self._append_log)
        tab.status_changed.connect(lambda state, k=p.key: self._set_tab_state(k, state))
        idx = self.tabs.insertTab(index, tab, _provider_icon(p.key), p.label)
        self._tabs_by_key[p.key] = tab
        # Default to dim until status returns.
        self.tabs.tabBar().setTabTextColor(idx, QColor("#888"))
        return tab

    def _retire_tab(self, tab: ProviderTab) -> None:
        """Detach a removed tab; keep it alive until its in-flight workers finish."""
        tab.status_changed.disconnect()
        tab.hide()
        self._retired_tabs.append(tab)
        tab.threads_idle.connect(lambda t=tab: self._reap_tab(t))
        if not tab._threads:
            self._reap_tab(tab)

    def _reap_tab(self, tab: ProviderTab) -> None:
        if tab not in self._retired_tabs:
            return
        self._retired_tabs.remove(tab)
        tab.deleteLater()

    def apply_enabled_providers(self) -> None:
        """Sync tabs with the enabled providers: add/remove live, keep canonical order."""
        wanted = providers.all_providers()
        wanted_keys = [p.key for p in wanted]
        cur = self.tabs.currentWidget()
        selected = next((k for k, t in self._tabs_by_key.items() if t is cur), None)
        for key in [k for k in self._tabs_by_key if k not in wanted_keys]:
            tab = self._tabs_by_key.pop(key)
            self.tabs.removeTab(self.tabs.indexOf(tab))
            self._retire_tab(tab)
        for i, p in enumerate(wanted):
            if p.key not in self._tabs_by_key:
                self._add_provider_tab(p, i)
        if selected in self._tabs_by_key:
            self.tabs.setCurrentWidget(self._tabs_by_key[selected])
        else:
            self.tabs.setCurrentIndex(0)

    def open_providers_dialog(self) -> None:
        dlg = self._build_providers_dialog()
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_providers_dialog(dlg)

    def _build_providers_dialog(self) -> _ProvidersDialog:
        dlg = _ProvidersDialog(self)
        dlg.setWindowTitle("Providers")
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        boxes: dict[str, QCheckBox] = {}
        for p in providers.all_providers(include_disabled=True):
            cb = QCheckBox(p.label)
            cb.setChecked(providers.is_enabled(p.key))
            boxes[p.key] = cb
            lay.addWidget(cb)

        def guard() -> None:
            checked = [cb for cb in boxes.values() if cb.isChecked()]
            for cb in boxes.values():
                cb.setEnabled(not (len(checked) == 1 and cb is checked[0]))

        for cb in boxes.values():
            cb.toggled.connect(guard)
        guard()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.provider_boxes = boxes
        return dlg

    def _apply_providers_dialog(self, dlg: _ProvidersDialog) -> None:
        boxes = dlg.provider_boxes
        changes = [(k, cb.isChecked()) for k, cb in boxes.items() if cb.isChecked() != providers.is_enabled(k)]
        # enable first so disabling never trips the "last provider" guard
        for key, on in sorted(changes, key=lambda c: not c[1]):
            ok, msg = providers.set_enabled(key, on)
            if not ok:
                self.statusBar().showMessage(msg)
        self.apply_enabled_providers()

    def _set_tab_state(self, key: str, state: AuthState) -> None:
        tab = self._tabs_by_key.get(key)
        if not tab:
            return
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return
        # Logo plus a small colored status dot in the corner.
        self.tabs.setTabIcon(idx, _provider_icon_with_state(key, state, size=22))
        color = "#26C281" if state == AuthState.AUTHENTICATED else "#8898A4"
        self.tabs.tabBar().setTabTextColor(idx, QColor(color))

    def _toggle_log(self) -> None:
        sizes = self.splitter.sizes()
        if sizes[1] > 0:
            # Remember last expanded size so we can restore it
            self._last_log_size = sizes[1]
            self.splitter.setSizes([sizes[0] + sizes[1], 0])
            self.collapse_log_btn.setText("Show")
        else:
            restore = getattr(self, "_last_log_size", 180) or 180
            total = sum(sizes)
            self.splitter.setSizes([max(total - restore, 200), restore])
            self.collapse_log_btn.setText("Hide")

    @Slot(str)
    def _append_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{ts}  {msg}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        for tab in [*self._tabs_by_key.values(), *self._retired_tabs]:
            for t in tab.findChildren(QThread):
                t.quit()
                t.wait(5000)
        g = self.geometry()
        settings.set("gui.window_geometry", [g.x(), g.y(), g.width(), g.height()])
        settings.set("gui.splitter_sizes", list(self.splitter.sizes()))
        super().closeEvent(event)


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from qt_material import apply_stylesheet

        apply_stylesheet(app, theme=THEME, extra={"density_scale": "-1"})
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not apply qt-material theme ({exc})", file=sys.stderr)
    win = MainWindow()
    # Layer our overlay QSS on top of qt-material's base stylesheet.
    base = app.styleSheet() or ""
    app.setStyleSheet(base + "\n" + OVERLAY_QSS)
    win.show()
    return app.exec()
