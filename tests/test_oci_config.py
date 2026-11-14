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


MESSY = (
    "# my creds\n[DEFAULT]\nuser   =  u1\n\n# prod below\n[PROD]\nuser=u2\n  region = uk-london-1\n\n\n"
    "[OTHER]\n; keep me\nuser=u3\n"
)


def test_add_profile_preserves_bytes(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(MESSY)
    _add("DEV")
    out = cfg.read_text()
    assert out.startswith(MESSY)
    assert out[len(MESSY):].startswith("\n[DEV]\n")


def test_overwrite_replaces_only_that_section(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(MESSY)
    _add("PROD", overwrite=True)
    out = cfg.read_text()
    head = MESSY[: MESSY.index("[PROD]")]
    tail = MESSY[MESSY.index("[OTHER]"):]
    assert out.startswith(head) and out.endswith(tail)
    assert "\n\n\n[OTHER]" in out
    assert oci_config.read_profiles()["PROD"]["user"] == UOCID


def test_overwrite_default(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(MESSY)
    _add("DEFAULT", overwrite=True)
    out = cfg.read_text()
    assert out.startswith("# my creds\n[DEFAULT]\nuser=" + UOCID)
    assert out.endswith(MESSY[MESSY.index("# prod below"):])


def test_no_trailing_newline(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[A]\nuser=x")
    _add("DEV")
    out = cfg.read_text()
    assert out.startswith("[A]\nuser=x\n\n[DEV]\n")
    assert oci_config.read_profiles()["A"]["user"] == "x"


def test_existing_0644_config_becomes_0600(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[A]\nuser=x\n")
    os.chmod(cfg, 0o644)
    _add("DEV")
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl missing")
def test_generate_key_pair_cleans_up_on_failure(tmp_path, monkeypatch):
    real = oci_config._run
    calls = {"n": 0}

    def flaky(args, data=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("openssl failed")
        return real(args, data)

    monkeypatch.setattr(oci_config, "_run", flaky)
    with pytest.raises(RuntimeError):
        oci_config.generate_key_pair(tmp_path, "k")
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(oci_config, "_run", real)
    priv, pub, _ = oci_config.generate_key_pair(tmp_path, "k")
    assert priv.exists() and pub.exists()


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl missing")
def test_generate_key_pair_refuses_existing_untouched(tmp_path):
    (tmp_path / "k.pem").write_text("precious")
    with pytest.raises(FileExistsError):
        oci_config.generate_key_pair(tmp_path, "k")
    assert (tmp_path / "k.pem").read_text() == "precious"
    assert not (tmp_path / "k_public.pem").exists()


def test_openssl_timeout_is_generic_error(monkeypatch):
    import subprocess

    def boom(*a, **k):
        assert k.get("timeout")
        raise subprocess.TimeoutExpired(a[0], k["timeout"], output=b"SECRET")

    monkeypatch.setattr(oci_config.subprocess, "run", boom)
    with pytest.raises(RuntimeError) as e:
        oci_config._run(["openssl", "x"], b"k")
    assert "timed out" in str(e.value) and "SECRET" not in str(e.value)
