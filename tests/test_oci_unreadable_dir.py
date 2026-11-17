"""An inaccessible ~/.oci directory yields a clean OCI error, never an exception."""

import os

import pytest

from nimbulo import settings
from nimbulo.models import AuthState
from nimbulo.providers import oci_config
from nimbulo.providers.oci import OCIProvider

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")


@pytest.fixture
def locked(tmp_path, monkeypatch, fake_run):
    d = tmp_path / ".oci"
    d.mkdir()
    (d / "config").write_text("[DEFAULT]\nregion=us-ashburn-1\n")
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", d / "config")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    d.chmod(0o000)
    yield d
    d.chmod(0o700)


def test_read_profiles_raises_config_error(locked):
    with pytest.raises(oci_config.OCIConfigError):
        oci_config.read_profiles()


def test_status_is_error_not_exception(locked):
    s = OCIProvider().status()
    assert s.state == AuthState.ERROR
    assert "could not be read" in s.message


def test_write_profile_raises_config_error(locked):
    with pytest.raises(oci_config.OCIConfigError):
        oci_config.write_api_key_profile(
            "p", user="u", tenancy="t", region="us-ashburn-1", key_file="/k.pem", fingerprint="aa"
        )


def test_api_key_login_fails_cleanly(locked, tmp_path):
    key = tmp_path / "k.pem"
    key.write_text("x")
    r = OCIProvider().login(
        method="api-key",
        profile="p",
        user="ocid1.user.oc1..a",
        tenancy="ocid1.tenancy.oc1..b",
        region="us-ashburn-1",
        key_file=str(key),
        fingerprint="aa:bb",
    )
    assert not r.ok
    assert "could not" in r.message
