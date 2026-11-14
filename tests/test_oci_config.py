import os
import shutil
import stat

import pytest

from cloud_login.providers import oci_config

UOCID = "ocid1.user.oc1..aaaa"
TOCID = "ocid1.tenancy.oc1..bbbb"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / ".oci" / "config"
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", p)
    return p


def _add(name, **kw):
    oci_config.write_api_key_profile(
        name, user=UOCID, tenancy=TOCID, region="us-ashburn-1",
        key_file="/k.pem", fingerprint="aa:bb", **kw,
    )


def test_read_profiles_missing_file_returns_empty(cfg):
    assert oci_config.read_profiles() == {}


def test_write_preserves_other_profiles(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[DEFAULT]\nuser=u1\nregion=eu-frankfurt-1\n\n[PROD]\nuser=u2\nregion=uk-london-1\n")
    _add("DEV")
    p = oci_config.read_profiles()
    assert set(p) == {"DEFAULT", "PROD", "DEV"}
    assert p["DEFAULT"]["user"] == "u1"
    assert p["PROD"]["region"] == "uk-london-1"
    assert p["DEV"]["user"] == UOCID and p["DEV"]["fingerprint"] == "aa:bb"


def test_write_refuses_overwrite_without_flag(cfg):
    _add("DEV")
    with pytest.raises(FileExistsError):
        _add("DEV")
    _add("DEV", overwrite=True)


def test_validate_ocid_accepts_and_rejects():
    assert oci_config.validate_ocid(UOCID, "user")
    assert oci_config.validate_ocid(TOCID, "tenancy")
    assert oci_config.validate_ocid("ocid1.user.oc1.iad.aaaa", "user")
    assert not oci_config.validate_ocid(TOCID, "user")
    assert not oci_config.validate_ocid("", "user")
    assert not oci_config.validate_ocid("   ", "user")
    assert not oci_config.validate_ocid(UOCID, "bogus")


def test_config_file_mode_0600(cfg):
    _add("DEV")
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    _add("DEV", overwrite=True)
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600


def test_known_regions():
    assert "us-ashburn-1" in oci_config.KNOWN_REGIONS


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl missing")
def test_generate_key_pair_creates_0600_private_key(tmp_path):
    priv, pub, fp = oci_config.generate_key_pair(tmp_path / "keys", "k")
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600
    assert priv.read_text().startswith("-----BEGIN")
    assert pub.read_text().startswith("-----BEGIN PUBLIC KEY")
    assert len(fp.split(":")) == 16
