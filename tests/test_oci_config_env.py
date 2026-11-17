"""$OCI_CLI_CONFIG_FILE is honored, as the oci CLI honors it."""

from nimbulo.models import AuthState
from nimbulo.providers import oci_config
from nimbulo.providers.oci import OCIProvider

FP = "aa:bb"


def test_default_path_without_env(monkeypatch):
    monkeypatch.delenv("OCI_CLI_CONFIG_FILE", raising=False)
    assert oci_config.config_path() == oci_config.OCI_CONFIG_PATH


def test_blank_env_is_ignored(monkeypatch):
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", "   ")
    assert oci_config.config_path() == oci_config.OCI_CONFIG_PATH


def test_env_path_is_read_and_tilde_expanded(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "oci.cfg"
    custom.parent.mkdir()
    custom.write_text("[work]\nregion=eu-frankfurt-1\n")
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", str(custom))
    assert list(oci_config.read_profiles()) == ["work"]
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", "~/elsewhere/oci.cfg")
    assert list(oci_config.read_profiles()) == ["work"]


def test_default_file_is_ignored_when_env_set(tmp_path, monkeypatch):
    oci_config.OCI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    oci_config.OCI_CONFIG_PATH.write_text("[default_only]\nregion=x\n")
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", str(tmp_path / "missing.cfg"))
    assert oci_config.read_profiles() == {}


def test_write_goes_to_env_file(tmp_path, monkeypatch):
    custom = tmp_path / "new-dir" / "oci.cfg"
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", str(custom))
    oci_config.write_api_key_profile(
        "p", user="ocid1.user.oc1..a", tenancy="ocid1.tenancy.oc1..b", region="us-ashburn-1",
        key_file="/k.pem", fingerprint=FP,
    )  # fmt: skip
    assert "[p]" in custom.read_text()
    assert not oci_config.OCI_CONFIG_PATH.exists()
    assert oct(custom.stat().st_mode & 0o777) == "0o600"


def test_status_uses_env_config(tmp_path, monkeypatch, fake_run):
    custom = tmp_path / "oci.cfg"
    custom.write_text("[work]\nregion=eu-frankfurt-1\n")
    monkeypatch.setenv("OCI_CLI_CONFIG_FILE", str(custom))
    s = OCIProvider().status(profile="work")
    assert s.state != AuthState.NOT_INSTALLED
    assert "not found in OCI config" not in (s.message or "")
    assert "work" in OCIProvider().profiles()
