"""The macOS menu bar iterates the provider registry; OCI must show up without starting a tray."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if sys.platform != "darwin":
    pytest.skip("menu bar is macOS-only", allow_module_level=True)

pytest.importorskip("PySide6.QtWidgets")


def test_menubar_lists_oci_provider():
    menubar = pytest.importorskip("cloud_login.ui.menubar")
    from cloud_login import providers

    keys = [p.key for p in providers.all_providers()]
    assert "oci" in keys
    oci = providers.get("oci")
    assert oci.label
    # The menu bar builds one controller and one status glyph per provider.
    assert hasattr(menubar, "STATE_GLYPHS")
    assert hasattr(menubar, "_ProviderController")
