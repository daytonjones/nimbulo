import base64
import json
import time
from datetime import datetime, timezone

import pytest

from cloud_login import settings
from cloud_login.models import AuthState
from cloud_login.providers import oci_config
from cloud_login.providers.oci import OCIProvider

UOCID = "ocid1.user.oc1..aaaa"
TOCID = "ocid1.tenancy.oc1..bbbb"


def _jwt(exp):
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b({'alg': 'RS256'})}.{b({'exp': exp})}.c2ln"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", tmp_path / "config")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    return tmp_path


def _write_config(env, text):
    (env / "config").write_text(text)


def _session_cfg(env, exp, name="SESS"):
    tok = env / "token"
    if exp is not None:
        tok.write_text(_jwt(exp))
    _write_config(
        env,
        f"[{name}]\nregion=eu-frankfurt-1\ntenancy={TOCID}\nsecurity_token_file={tok}\nkey_file={env}/k.pem\n",
    )


def _apikey_cfg(env, name="API"):
    _write_config(
        env,
        f"[{name}]\nuser={UOCID}\ntenancy={TOCID}\nregion=us-ashburn-1\nfingerprint=aa:bb\nkey_file={env}/k.pem\n",
    )


def test_status_not_installed(fake_run, env):
    fake_run.installed = False
    assert OCIProvider().status().state == AuthState.NOT_INSTALLED


def test_status_no_config_file_unauthenticated(fake_run, env):
    st = OCIProvider().status()
    assert st.state == AuthState.UNAUTHENTICATED
    assert "session" in st.message


def test_status_session_valid(fake_run, env):
    exp = int(time.time()) + 3600
    _session_cfg(env, exp)
    fake_run.add(["oci", "session", "validate", "--profile", "SESS", "--local"], rc=0)
    st = OCIProvider().status()
    assert st.state == AuthState.AUTHENTICATED
    assert st.active_profile == "SESS" and st.profiles == ["SESS"]
    assert st.identity.expires_at == datetime.fromtimestamp(exp, timezone.utc)


def test_status_session_valid_with_permissions_warning(fake_run, env):
    _session_cfg(env, int(time.time()) + 3600)
    fake_run.add(
        ["oci", "session", "validate", "--profile", "SESS", "--local"],
        rc=0,
        err="WARNING: Permissions on /x/k.pem are too open.",
    )
    st = OCIProvider().status()
    assert st.state == AuthState.AUTHENTICATED
    assert "Permissions" in st.message


def test_status_session_expired(fake_run, env):
    _session_cfg(env, int(time.time()) - 60)
    fake_run.add(["oci", "session", "validate", "--profile", "SESS", "--local"], rc=1, err="expired")
    assert OCIProvider().status().state == AuthState.EXPIRED


def test_status_session_missing_token_file(fake_run, env):
    _session_cfg(env, None)
    st = OCIProvider().status()
    assert st.state == AuthState.UNAUTHENTICATED
    assert "token" in st.message


def test_status_session_malformed_token(fake_run, env):
    _session_cfg(env, 1)
    (env / "token").write_text("not-a-jwt")
    st = OCIProvider().status()
    assert st.state in (AuthState.ERROR, AuthState.UNAUTHENTICATED)
    assert "not-a-jwt" not in st.message


def test_status_api_key_ok(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "API"], rc=0, out='{"data": []}')
    st = OCIProvider().status()
    assert st.state == AuthState.AUTHENTICATED
    assert st.identity.account == TOCID and st.identity.user == UOCID
    assert st.identity.extra["region"] == "us-ashburn-1"


def test_status_api_key_offline_is_error(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "API"], rc=124, err="timeout after 15s")
    assert OCIProvider().status().state == AuthState.ERROR


def test_status_api_key_auth_failure(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(
        ["oci", "iam", "region", "list", "--profile", "API"],
        rc=1,
        err='ServiceError: {"code": "NotAuthenticated", "status": 401}',
    )
    assert OCIProvider().status().state == AuthState.UNAUTHENTICATED


def test_status_profile_selection(fake_run, env):
    _write_config(env, "[DEFAULT]\nregion=us-ashburn-1\n[A]\nregion=x\n[B]\nregion=y\n")
    fake_run.add(["oci", "iam", "region", "list"], rc=0)
    p = OCIProvider()
    assert p.status().active_profile == "DEFAULT"
    settings.set("last_profile.oci", "B")
    assert p.status().active_profile == "B"
    assert p.status("A").active_profile == "A"


def test_login_session_runs_authenticate(fake_run, env):
    fake_run.add(
        ["oci", "session", "authenticate", "--profile-name", "P", "--region", "eu-paris-1"],
        rc=0,
        out="Opening browser\nDone",
    )
    lines = []
    res = OCIProvider().login("session", on_output=lines.append, profile="P", region="eu-paris-1")
    assert res.ok
    assert fake_run.streamed == [["oci", "session", "authenticate", "--profile-name", "P", "--region", "eu-paris-1"]]
    assert "Opening browser" in lines


def test_login_session_default_region(fake_run, env):
    _write_config(env, "[P]\nregion=ap-tokyo-1\n")
    OCIProvider().login("session", profile="P")
    assert fake_run.streamed[0][-1] == "ap-tokyo-1"
    OCIProvider().login("session", profile="Q")
    assert fake_run.streamed[1][-1] == "us-ashburn-1"


def test_login_profile_method_only_validates(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "API"], rc=0)
    res = OCIProvider().login("profile", profile="API")
    assert res.ok
    assert fake_run.streamed == []
    assert all(c[:3] != ["oci", "session", "authenticate"] for c in fake_run.calls)


def test_login_methods():
    assert OCIProvider().login_methods() == ["session", "profile", "api-key"]


def test_logout_session_runs_terminate(fake_run, env):
    _session_cfg(env, int(time.time()) + 3600)
    fake_run.add(["oci", "session", "terminate", "--profile", "SESS"], rc=0)
    res = OCIProvider().logout("SESS")
    assert res.ok
    assert ["oci", "session", "terminate", "--profile", "SESS"] in fake_run.calls


def test_logout_api_key_profile_not_supported_message(fake_run, env):
    _apikey_cfg(env)
    res = OCIProvider().logout("API")
    assert not res.ok and "session" in res.message
    assert fake_run.calls == []
