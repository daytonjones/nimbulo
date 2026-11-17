"""The global guard keeps tests away from the real ~/.oci and the real oci CLI."""

import os
import subprocess
from pathlib import Path

import pytest
from conftest import _REAL_HOME, RealOCIBlocked

from nimbulo import settings
from nimbulo.providers import oci, oci_config


def test_home_and_paths_are_redirected(_isolate_home):
    home = _isolate_home
    assert Path(os.path.expanduser("~")) == home != _REAL_HOME
    for p in (oci_config.OCI_CONFIG_PATH, oci.OCI_KEYS_DIR, settings.SETTINGS_FILE):
        assert home in p.parents, p
    assert not oci_config.OCI_CONFIG_PATH.exists()
    assert oci_config.read_profiles() == {}


def test_real_oci_cli_is_blocked():
    with pytest.raises(RealOCIBlocked):
        subprocess.run(["oci", "iam", "region", "list"], capture_output=True)
    with pytest.raises(RealOCIBlocked):
        subprocess.Popen(["/opt/homebrew/bin/oci", "session", "validate"])


def test_other_commands_still_run():
    assert subprocess.run(["true"]).returncode == 0
