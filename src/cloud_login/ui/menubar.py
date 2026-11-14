"""macOS menu bar (system tray) app.

Lightweight resident process that shows aggregate cloud auth health in the
menu bar. Click any provider to open the main GUI focused on that tab.

Threading note:
    We use a per-provider _ProviderController(QObject) so every cross-thread
    signal connection has a proper QObject receiver with main-thread affinity.
    Lambda receivers with Qt.QueuedConnection have ambiguous thread affinity
    in PySide6 and trigger `QBasicTimer::start` warnings (and eventually
    crashes) when fired repeatedly.
"""
from __future__ import annotations

import sys
from typing import Callable

from PySide6.QtCore import QObject, QSize, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import providers, settings
from ..models import AuthState, ProviderStatus, format_expires
from ..providers.base import Provider
from .gui import (
    MainWindow,
    OVERLAY_QSS,
    STATE_COLORS,
    THEME,
    _provider_icon_with_state,
    _Worker,
)


STATE_GLYPHS = {
    AuthState.AUTHENTICATED: "✓",
    AuthState.EXPIRED: "⏱",
    AuthState.UNAUTHENTICATED: "✗",
    AuthState.ERROR: "!",
    AuthState.NOT_INSTALLED: "—",
    AuthState.UNKNOWN: "…",
}

_SEVERITY = {
    AuthState.AUTHENTICATED: 0,
    AuthState.UNKNOWN: 1,
    AuthState.NOT_INSTALLED: 1,
    AuthState.EXPIRED: 2,
    AuthState.UNAUTHENTICATED: 3,
    AuthState.ERROR: 3,
}


def _status_icon(state: AuthState, size: int = 22) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(STATE_COLORS[state]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, size - 6, size - 6)
    painter.end()
    return QIcon(pix)


def _aggregate(states: list[AuthState]) -> AuthState:
    if not states:
        return AuthState.UNKNOWN
    return max(states, key=lambda s: _SEVERITY.get(s, 0))


# ---------------------------------------------------------------------------
# Per-provider controller — owns its worker thread, re-emits results as a
# main-thread signal so the menu bar app only ever sees clean (key, status)
# events.
# ---------------------------------------------------------------------------


class _ProviderController(QObject):
    result = Signal(str, object)  # (key, ProviderStatus)

    def __init__(self, provider: Provider, parent: QObject) -> None:
        super().__init__(parent)
        self._provider = provider
        self._thread: QThread | None = None
        self._worker: _Worker | None = None

    @property
    def key(self) -> str:
        return self._provider.key

    def start(self, profile: str | None = None) -> None:
        # Debounce — if a previous status check is still running, skip.
        if self._thread is not None and self._thread.isRunning():
            return
        kwargs: dict = {"profile": profile} if profile else {}
        thread = QThread(self)
        worker = _Worker(self._provider.status, (), kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_refs, Qt.ConnectionType.QueuedConnection)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _on_done(self, status: object) -> None:
        self.result.emit(self.key, status)

    @Slot(str)
    def _on_failed(self, _msg: str) -> None:
        # Surface a synthetic ERROR status so the icon doesn't get stuck.
        from ..models import Identity  # avoid top-level cycle

        self.result.emit(
            self.key,
            ProviderStatus(provider=self.key, state=AuthState.ERROR, identity=Identity(), message=_msg),
        )

    @Slot()
    def _clear_refs(self) -> None:
        self._thread = None
        self._worker = None


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------


class MenuBarApp(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self._gui_window: MainWindow | None = None
        self._states: dict[str, AuthState] = {}

        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("cloud-login")
        self.tray.setIcon(_status_icon(AuthState.UNKNOWN))

        # One controller per provider; each owns its worker thread.
        self._controllers: dict[str, _ProviderController] = {}
        for p in providers.all_providers():
            c = _ProviderController(p, self)
            c.result.connect(self._on_status)
            self._controllers[p.key] = c

        self.menu = QMenu()
        self._provider_actions: dict[str, QAction] = {}
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self.refresh_all)
        self.tray.show()

        # Poll every 2 minutes so expirations are reflected even if the user
        # never opens the menu.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_all)
        self._timer.start(2 * 60 * 1000)
        self.refresh_all()

    # --- menu construction ------------------------------------------------

    def _build_menu(self) -> None:
        header = QAction("Cloud Login", self.menu)
        header.setEnabled(False)
        self.menu.addAction(header)
        self.menu.addSeparator()

        for p in providers.all_providers():
            act = QAction(_provider_icon_with_state(p.key, None), f"{p.label}  …", self.menu)
            act.triggered.connect(self._make_open_handler(p.key))
            self.menu.addAction(act)
            self._provider_actions[p.key] = act

        self.menu.addSeparator()

        refresh_act = QAction("Refresh All", self.menu)
        refresh_act.setShortcut("Ctrl+R")
        refresh_act.triggered.connect(self.refresh_all)
        self.menu.addAction(refresh_act)

        open_act = QAction("Open Cloud Login…", self.menu)
        open_act.triggered.connect(self._open_gui)
        self.menu.addAction(open_act)

        self.menu.addSeparator()
        quit_act = QAction("Quit cloud-login", self.menu)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.app.quit)
        self.menu.addAction(quit_act)

    def _make_open_handler(self, key: str) -> Callable[[], None]:
        def handler(_checked: bool = False) -> None:
            self._open_gui(focus=key)

        return handler

    # --- refresh ----------------------------------------------------------

    @Slot()
    def refresh_all(self) -> None:
        for key, c in self._controllers.items():
            profile = settings.get(f"last_profile.{key}")
            c.start(profile=profile)

    @Slot(str, object)
    def _on_status(self, key: str, status: ProviderStatus) -> None:
        self._states[key] = status.state
        act = self._provider_actions.get(key)
        if act is not None:
            act.setIcon(_provider_icon_with_state(key, status.state))
            act.setText(self._format_provider_line(status))
        agg = _aggregate(list(self._states.values()))
        self.tray.setIcon(_status_icon(agg))
        tip_lines = [
            f"{providers.get(k).label}: {self._states[k].value}"
            for k in self._states
        ]
        self.tray.setToolTip("cloud-login\n" + "\n".join(tip_lines))

    def _format_provider_line(self, s: ProviderStatus) -> str:
        provider = providers.get(s.provider)
        glyph = STATE_GLYPHS.get(s.state, "?")
        extras: list[str] = []
        if s.identity:
            if s.identity.expires_at:
                label, _ = format_expires(s.identity.expires_at)
                extras.append(label)
            elif s.identity.account:
                extras.append(str(s.identity.account))
            elif s.identity.user:
                extras.append(str(s.identity.user))
        if s.active_profile and not extras:
            extras.append(s.active_profile)
        tail = "  ·  ".join(extras) if extras else s.state.value
        return f"{provider.label}  {glyph}  {tail}"

    # --- GUI window -------------------------------------------------------

    @Slot()
    def _open_gui(self, focus: str | None = None) -> None:
        if self._gui_window is None:
            self._gui_window = MainWindow()
        win = self._gui_window
        win.show()
        win.raise_()
        win.activateWindow()
        if focus:
            for i in range(win.tabs.count()):
                widget = win.tabs.widget(i)
                if getattr(widget, "provider", None) and widget.provider.key == focus:
                    win.tabs.setCurrentIndex(i)
                    break


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if sys.platform == "darwin":
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory

            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except (ImportError, AttributeError):
            pass

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("error: no system tray available on this platform", file=sys.stderr)
        return 1

    try:
        from qt_material import apply_stylesheet

        apply_stylesheet(app, theme=THEME, extra={"density_scale": "-1"})
        app.setStyleSheet((app.styleSheet() or "") + "\n" + OVERLAY_QSS)
    except Exception:  # noqa: BLE001
        pass

    _ = MenuBarApp(app)
    return app.exec()
