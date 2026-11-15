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
    p = OCIProvider()
    assert p.status().active_profile == "DEFAULT"
    settings.set("last_profile.oci", "B")
    assert p.status().active_profile == "B"
    assert p.status("A").active_profile == "A"
    assert fake_run.calls == []  # incomplete profiles are reported without calling oci


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
    _write_config(env, "[P]\nregion=ap-tokyo-1\nsecurity_token_file=/t\n")
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
    res = OCIProvider().logout("SESS", confirm=True)
    assert res.ok
    assert ["oci", "session", "terminate", "--profile", "SESS"] in fake_run.calls


def test_logout_api_key_profile_not_supported_message(fake_run, env):
    _apikey_cfg(env)
    res = OCIProvider().logout("API")
    assert not res.ok and "session" in res.message
    assert fake_run.calls == []


# --- final review fix wave ----------------------------------------------------

from cloud_login.providers import oci  # noqa: E402

FULL_DEFAULT = f"[DEFAULT]\nuser={UOCID}\ntenancy={TOCID}\nfingerprint=aa:bb\nkey_file=/k.pem\nregion=us-ashburn-1\n"
SERVICE_ERROR = """WARNING: Permissions on /x/k.pem are too open.
ServiceError:
{
    "client_version": "Oracle-PythonCLI/3.96.2",
    "code": "NotAuthenticated",
    "message": "The required information to complete authentication was not provided.",
    "opc-request-id": "ABC/DEF",
    "operation_name": "list_regions",
    "status": 401,
    "target_service": "identity"
}
"""


def test_status_profile_inheriting_default_is_complete(fake_run, env):
    _write_config(env, FULL_DEFAULT + "\n[PROD]\nregion=eu-frankfurt-1\n")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "PROD"], rc=0, out="{}")
    st = OCIProvider().status("PROD")
    assert st.state == AuthState.AUTHENTICATED, st.message
    assert st.identity.account == TOCID and st.identity.extra["region"] == "eu-frankfurt-1"


def test_session_detected_via_default_security_token_file(fake_run, env):
    tok = env / "token"
    tok.write_text(_jwt(int(time.time()) + 3600))
    _write_config(env, f"[DEFAULT]\nsecurity_token_file={tok}\ntenancy={TOCID}\n\n[S]\nregion=eu-paris-1\n")
    fake_run.add(["oci", "session", "validate", "--profile", "S", "--local"], rc=0)
    st = OCIProvider().status("S")
    assert st.state == AuthState.AUTHENTICATED
    assert st.identity.extra["kind"] == "session"


@pytest.mark.parametrize(
    "content",
    [b"[A]\nuser=x\n[A]\nuser=y\n", b"garbage text\n", b"\xff\xfe\x00\x81binary"],
)
def test_malformed_config_is_error_status_not_exception(fake_run, env, content):
    (env / "config").write_bytes(content)
    p = OCIProvider()
    st = p.status()
    assert st.state == AuthState.ERROR
    assert "could not be parsed" in st.message and "\n" not in st.message
    assert p.profiles() == []
    assert p.api_key_defaults()["region"] == "us-ashburn-1"
    assert p.list_secondary() == []
    assert not p.logout().ok
    assert not p.login("session", profile="X").ok
    assert fake_run.calls == []


def test_unreadable_config_is_error_status(fake_run, env):
    (env / "config").mkdir()
    st = OCIProvider().status()
    assert st.state == AuthState.ERROR and "could not be parsed" in st.message


def test_malformed_oci_config_does_not_break_cli_status(fake_run, env, monkeypatch):
    from typer.testing import CliRunner

    from cloud_login import providers
    from cloud_login.models import ProviderStatus
    from cloud_login.ui.cli import app

    (env / "config").write_text("[A]\n[A]\n")

    def unknown(self, profile=None):
        return ProviderStatus(self.key, AuthState.UNKNOWN)

    for p in providers.all_providers():
        if p.key != "oci":
            monkeypatch.setattr(type(p), "status", unknown)
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "aws" in result.output and "error" in result.output


def test_service_error_surfaces_code_and_message(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "API"], rc=1, err=SERVICE_ERROR)
    st = OCIProvider().status()
    assert st.state == AuthState.UNAUTHENTICATED
    assert st.message == "NotAuthenticated: The required information to complete authentication was not provided."


def test_cli_error_fallback_skips_braces_and_indented_warnings():
    assert oci.OCIProvider._last_line("  WARNING: x\nreal problem\n}\n") == "real problem"
    assert oci.OCIProvider._last_line("ServiceError:\n{ not json\n}\n") == "{ not json"
    assert oci.OCIProvider._last_line("", "only stdout") == "only stdout"


def test_session_validate_timeout_is_error(fake_run, env):
    _session_cfg(env, int(time.time()) + 3600)
    fake_run.add(["oci", "session", "validate", "--profile", "SESS", "--local"], rc=124, err="timeout after 20s")
    assert OCIProvider().status().state == AuthState.ERROR


# C1 -- session login must not silently convert an API-key profile


def _auth_cmd(profile, region):
    return ["oci", "session", "authenticate", "--profile-name", profile, "--region", region]


def test_session_login_refuses_api_key_profile(fake_run, env):
    _apikey_cfg(env)
    res = OCIProvider().login("session", profile="API")
    assert not res.ok
    assert isinstance(res, oci.ConfirmationRequired)
    assert "API" in res.message and "API-key" in res.message and "comments" in res.message
    assert "profile" in res.message and "new profile name" in res.message
    assert not any(c[:3] == ["oci", "session", "authenticate"] for c in fake_run.calls)


def test_session_login_refuses_profile_inheriting_api_key_default(fake_run, env):
    _write_config(env, FULL_DEFAULT)
    res = OCIProvider().login("session")  # CLI fallback profile DEFAULT
    assert isinstance(res, oci.ConfirmationRequired) and "DEFAULT" in res.message
    assert fake_run.calls == []


def test_session_login_api_key_profile_allowed_with_overwrite(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(_auth_cmd("API", "us-ashburn-1"), rc=0)
    res = OCIProvider().login("session", profile="API", overwrite=True)
    assert res.ok and fake_run.streamed == [_auth_cmd("API", "us-ashburn-1")]


def test_session_login_existing_session_profile_allowed(fake_run, env):
    _session_cfg(env, int(time.time()) - 60)
    fake_run.add(_auth_cmd("SESS", "eu-frankfurt-1"), rc=0)
    assert OCIProvider().login("session", profile="SESS").ok
    assert fake_run.streamed == [_auth_cmd("SESS", "eu-frankfurt-1")]


def test_session_login_new_profile_allowed(fake_run, env):
    _apikey_cfg(env)
    fake_run.add(_auth_cmd("NEW", "us-ashburn-1"), rc=0)
    res = OCIProvider().login("session", profile="NEW")
    assert res.ok and not isinstance(res, oci.ConfirmationRequired)


# I7 -- logout removes the profile


def test_logout_session_requires_confirmation(fake_run, env):
    _session_cfg(env, int(time.time()) + 3600)
    res = OCIProvider().logout("SESS")
    assert isinstance(res, oci.ConfirmationRequired) and not res.ok
    assert "REMOVED" in res.message and "SESS" in res.message
    assert fake_run.calls == []


def test_logout_confirmed_says_profile_removed(fake_run, env):
    _session_cfg(env, int(time.time()) + 3600)
    fake_run.add(["oci", "session", "terminate", "--profile", "SESS"], rc=0)
    res = OCIProvider().logout("SESS", confirm=True)
    assert res.ok and "removed" in res.message and "SESS" in res.message


# M4 -- profile method records the profile


def test_login_profile_method_records_last_profile(fake_run, env):
    _write_config(env, FULL_DEFAULT + "\n[PROD]\nregion=eu-frankfurt-1\n")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "PROD"], rc=0)
    assert OCIProvider().login("profile", profile="PROD").ok
    assert settings.get("last_profile.oci") == "PROD"
    assert OCIProvider().status().active_profile == "PROD"


def test_login_profile_method_unknown_profile_not_recorded(fake_run, env):
    _write_config(env, FULL_DEFAULT)
    assert not OCIProvider().login("profile", profile="NOPE").ok
    assert settings.get("last_profile.oci") is None
