"""Encrypted keys without a pass_phrase never make the oci CLI prompt/hang."""

import sys

from nimbulo.models import AuthState
from nimbulo.providers import oci_config
from nimbulo.providers.base import Provider
from nimbulo.providers.oci import OCIProvider

ENC_PKCS8 = "-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIIFH\n-----END ENCRYPTED PRIVATE KEY-----\n"
ENC_LEGACY = (
    "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,AB\n\nxx\n"
    "-----END RSA PRIVATE KEY-----\n"
)
PLAIN = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"


def _profile(tmp_path, key_text, extra=""):
    key = tmp_path / "k.pem"
    key.write_text(key_text)
    oci_config.OCI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    oci_config.OCI_CONFIG_PATH.write_text(
        f"[p]\nuser=ocid1.user.oc1..a\nfingerprint=aa:bb\ntenancy=ocid1.tenancy.oc1..b\n"
        f"region=us-ashburn-1\nkey_file={key}\n{extra}"
    )


def test_key_is_encrypted_detection(tmp_path):
    for text, expected in ((ENC_PKCS8, True), (ENC_LEGACY, True), (PLAIN, False)):
        f = tmp_path / "k"
        f.write_text(text)
        assert oci_config.key_is_encrypted(f) is expected
    assert oci_config.key_is_encrypted(tmp_path / "missing") is False


def test_encrypted_key_without_passphrase_is_a_clean_error_and_oci_not_run(tmp_path, fake_run):
    _profile(tmp_path, ENC_PKCS8)
    s = OCIProvider().status("p")
    assert s.state == AuthState.ERROR
    assert "pass_phrase" in s.message and "prompt" in s.message
    assert fake_run.calls == []  # never invoked oci


def test_encrypted_key_with_passphrase_proceeds(tmp_path, fake_run):
    _profile(tmp_path, ENC_PKCS8, "pass_phrase=secret\n")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "p"], rc=0, out="{}")
    assert OCIProvider().status("p").state == AuthState.AUTHENTICATED


def test_profile_login_reports_the_passphrase_problem(tmp_path, fake_run):
    _profile(tmp_path, ENC_LEGACY)
    r = OCIProvider().login(method="profile", profile="p")
    assert not r.ok and "pass_phrase" in r.message


def test_oci_commands_run_without_a_terminal(tmp_path, fake_run):
    _profile(tmp_path, PLAIN)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "p"], rc=0, out="{}")
    OCIProvider().status("p")
    assert fake_run.no_tty and all(fake_run.no_tty)


def test_no_tty_really_detaches_the_child():
    code = "import sys\ntry:\n    open('/dev/tty').close(); print('TTY')\nexcept OSError:\n    print('NOTTY')\n"
    rc, out, _ = Provider._run([sys.executable, "-c", code], no_tty=True)
    assert rc == 0 and out.strip() == "NOTTY"
    # stdin is closed too: a read returns EOF immediately
    rc, out, _ = Provider._run([sys.executable, "-c", "import sys;print(repr(sys.stdin.read()))"], no_tty=True)
    assert out.strip() == "''"


def test_default_run_is_unchanged():
    rc, out, _ = Provider._run([sys.executable, "-c", "print('hi')"])
    assert (rc, out.strip()) == (0, "hi")
