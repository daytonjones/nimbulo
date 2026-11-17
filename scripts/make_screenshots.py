"""Regenerate the README screenshots in docs/images/ from FAKE demo data.

    uv run python scripts/make_screenshots.py [--only gui|tui|cli]

Safety: HOME is pointed at a throwaway directory *before* nimbulo is imported, and every
provider is replaced by a stub subclass whose status()/profiles()/list_secondary() return
canned objects. No vendor CLI is executed and no real ~/.aws, ~/.oci, gcloud config or
keychain entry is read. Production code is not modified; the registry is patched in-process.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"

# Isolate from the real environment before anything from nimbulo is imported.
_TMP_HOME = tempfile.mkdtemp(prefix="nimbulo-shots-")
os.environ["HOME"] = _TMP_HOME
for _var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "OCI_CLI_PROFILE", "DIGITALOCEAN_TOKEN"):
    os.environ.pop(_var, None)

from nimbulo import providers, settings  # noqa: E402
from nimbulo.models import AuthState, Identity, ProviderStatus  # noqa: E402
from nimbulo.providers import (  # noqa: E402
    AWSProvider,
    AzureProvider,
    DigitalOceanProvider,
    GCPProvider,
    OCIProvider,
    VultrProvider,
)

settings.CONFIG_DIR = Path(_TMP_HOME) / "config"
settings.SETTINGS_FILE = settings.CONFIG_DIR / "settings.json"


def _in(**kw: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kw)


def demo_statuses() -> dict[str, ProviderStatus]:
    """Canned, entirely fictional status objects (a mix of states)."""
    return {
        "aws": ProviderStatus(
            "aws",
            AuthState.AUTHENTICATED,
            Identity(
                account="123456789012",
                user="arn:aws:sts::123456789012:assumed-role/Demo/demo",
                expires_at=_in(hours=3, minutes=42),
            ),
            active_profile="demo-sso",
            profiles=["default", "demo-sso", "demo-readonly"],
        ),
        "gcp": ProviderStatus(
            "gcp",
            AuthState.AUTHENTICATED,
            Identity(account="demo-project-1234", user="demo@example.com"),
            active_profile="default",
            profiles=["default", "demo-staging"],
        ),
        "azure": ProviderStatus(
            "azure",
            AuthState.AUTHENTICATED,
            Identity(account="Demo Subscription", user="demo@example.com", expires_at=_in(minutes=18)),
            active_profile="Demo Subscription",
        ),
        "oci": ProviderStatus(
            "oci",
            AuthState.EXPIRED,
            Identity(account="ocid1.tenancy.oc1..exampledemo", user="demo@example.com", expires_at=_in(minutes=-12)),
            active_profile="DEMO",
            profiles=["DEMO"],
            message="session token expired",
        ),
        "vultr": ProviderStatus("vultr", AuthState.UNAUTHENTICATED, message="no API key stored"),
        "digitalocean": ProviderStatus(
            "digitalocean",
            AuthState.AUTHENTICATED,
            Identity(account="demo@example.com", user="demo@example.com"),
            message="team: demo-team",
        ),
    }


SECONDARY = {
    "gcp": [
        ("demo-project-1234", "Demo Project"),
        ("demo-staging-5678", "Demo Staging"),
        ("demo-sandbox-9012", "Demo Sandbox"),
    ],
    "azure": [
        ("00000000-0000-0000-0000-000000000001", "Demo Subscription"),
        ("00000000-0000-0000-0000-000000000002", "Demo Dev/Test"),
    ],
    "oci": [("ocid1.compartment.oc1..exampledemo", "demo-compartment")],
}


def _stub(real_cls: type, status: ProviderStatus) -> type:
    """Subclass a real provider (keeps its labels/UI hints) with canned data and no I/O."""

    class Stub(real_cls):  # type: ignore[misc, valid-type]
        def status(self, profile=None):
            return status

        def profiles(self):
            return list(status.profiles)

        def list_secondary(self):
            return list(SECONDARY.get(self.key, []))

        def cli_available(self):
            return True

        def api_key_defaults(self):
            return {"profile": "DEMO", "region": "us-ashburn-1", "key_dir": "~/.oci/keys"}

    Stub.__name__ = f"Demo{real_cls.__name__}"
    return Stub


def install_stubs() -> None:
    st = demo_statuses()
    classes = [AWSProvider, GCPProvider, AzureProvider, OCIProvider, VultrProvider, DigitalOceanProvider]
    stubs = [_stub(c, st[c.key]) for c in classes]
    providers._all = lambda: [s() for s in stubs]  # type: ignore[assignment]


# --- CLI ----------------------------------------------------------------------


def make_cli() -> None:
    from rich.console import Console

    from nimbulo.ui.cli import _render_status

    console = Console(record=True, width=170, force_terminal=True, color_system="truecolor", file=open(os.devnull, "w"))
    console.print("[dim]$ nimbulo status[/]")
    console.print(_render_status([p.status() for p in providers.all_providers()]))
    console.save_svg(str(OUT / "cli-status.svg"), title="nimbulo status")


# --- TUI ----------------------------------------------------------------------


def make_tui() -> None:
    from nimbulo.ui import tui

    async def go() -> str:
        app = tui.CloudLoginTUI()
        async with app.run_test(size=(110, 26)) as pilot:
            for _ in range(40):
                await pilot.pause()
                await asyncio.sleep(0.05)
            app.screen.scroll_home(animate=False)  # show the tabs + status, not the log below the fold
            await pilot.pause()
            return app.export_screenshot(title="nimbulo tui")

    (OUT / "tui.svg").write_text(asyncio.run(go()))


# --- GUI ----------------------------------------------------------------------


def make_gui() -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from nimbulo.ui import gui

    app = QApplication.instance() or QApplication(sys.argv)
    from qt_material import apply_stylesheet

    apply_stylesheet(app, theme=gui.THEME, extra={"density_scale": "-1"})
    win = gui.MainWindow()
    app.setStyleSheet((app.styleSheet() or "") + "\n" + gui.OVERLAY_QSS)
    win.resize(1040, 780)
    win.show()
    win.splitter.setSizes([540, 200])

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def grab(name: str) -> None:
        pump(1.0)
        pix = win.grab()
        if pix.width() > 1600:
            pix = pix.scaledToWidth(1600, gui.Qt.TransformationMode.SmoothTransformation)
        pix.save(str(OUT / name), "PNG")

    pump(3.0)  # let the worker threads deliver status
    win.tabs.setCurrentIndex(0)
    grab("gui.png")
    win.tabs.setCurrentIndex(1)  # Google Cloud: project picker
    pump(1.0)
    grab("gui-gcp.png")

    QTimer.singleShot(0, win.close)
    win.close()
    pump(0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["gui", "tui", "cli"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    install_stubs()
    if args.only in (None, "cli"):
        make_cli()
    if args.only in (None, "tui"):
        make_tui()
    if args.only in (None, "gui"):
        make_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
