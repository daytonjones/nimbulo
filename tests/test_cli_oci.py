import json

import pytest
from typer.testing import CliRunner

from cloud_login import settings
from cloud_login.providers import oci, oci_config
from cloud_login.ui.cli import app

runner = CliRunner()

T = "ocid1.tenancy.oc1..root"
U = "ocid1.user.oc1..alice"
DEV = "ocid1.compartment.oc1..dev"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", tmp_path / "config")
    monkeypatch.setattr(oci, "OCI_KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    oci._compartment_cache.clear()
    return tmp_path


def test_cli_status_includes_oci(env, fake_run):
    fake_run.installed = False
    result = runner.invoke(app, ["status", "-p", "oci"])
    assert result.exit_code == 0, result.output
    assert "oci" in result.output.lower()


def test_cli_login_oci_session(env, fake_run):
    fake_run.add(
        ["oci", "session", "authenticate", "--profile-name", "my-profile", "--region", "us-ashburn-1"],
        rc=0,
        out="Config written",
    )
    result = runner.invoke(
        app, ["login", "oci", "-m", "session", "--profile", "my-profile", "--region", "us-ashburn-1"]
    )
    assert result.exit_code == 0, result.output
    assert fake_run.streamed == [
        ["oci", "session", "authenticate", "--profile-name", "my-profile", "--region", "us-ashburn-1"]
    ]


def test_cli_login_oci_api_key_flags(env, fake_run):
    fake_run.add(["oci", "iam", "region", "list", "--profile", "ci"], rc=0, out="{}")
    result = runner.invoke(
        app,
        [
            "login",
            "oci",
            "-m",
            "api-key",
            "--profile",
            "ci",
            "--user",
            U,
            "--tenancy",
            T,
            "--region",
            "us-ashburn-1",
            "--generate-key",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "BEGIN PUBLIC KEY" in result.output
    assert "Add API key" in result.output
    assert "BEGIN PRIVATE KEY" not in result.output and "BEGIN RSA PRIVATE" not in result.output
    assert "ci" in oci_config.read_profiles()


def test_cli_login_oci_api_key_prompts_missing(env, fake_run):
    fake_run.add(["oci", "iam", "region", "list", "--profile", "pp"], rc=0, out="{}")
    answers = "\n".join(["pp", U, T, "us-ashburn-1", "y"]) + "\n"
    result = runner.invoke(app, ["login", "oci", "-m", "api-key"], input=answers)
    assert result.exit_code == 0, result.output
    assert "pp" in oci_config.read_profiles()


def test_cli_login_oci_failure_exit_and_no_markup(env, fake_run):
    result = runner.invoke(
        app,
        [
            "login",
            "oci",
            "-m",
            "api-key",
            "--profile",
            "x",
            "--user",
            "bad",
            "--tenancy",
            T,
            "--region",
            "[bold]",
            "--generate-key",
        ],
    )
    assert result.exit_code == 1


def test_cli_compartments_and_set_compartment(env, fake_run):
    (env / "config").write_text(f"[P1]\ntenancy={T}\nregion=us-ashburn-1\n")
    fake_run.add(
        [
            "oci",
            "iam",
            "compartment",
            "list",
            "--compartment-id",
            T,
            "--compartment-id-in-subtree",
            "true",
            "--all",
            "--lifecycle-state",
            "ACTIVE",
            "--output",
            "json",
            "--profile",
            "P1",
        ],
        rc=0,
        out=json.dumps({"data": [{"id": DEV, "name": "dev", "compartment-id": T, "lifecycle-state": "ACTIVE"}]}),
    )
    result = runner.invoke(app, ["compartments"])
    assert result.exit_code == 0, result.output
    assert DEV in result.output.replace("\n", "")
    assert "root/dev" in result.output


def test_cli_set_compartment(env, fake_run):
    (env / "config").write_text(f"[P1]\ntenancy={T}\nregion=us-ashburn-1\n")
    result = runner.invoke(app, ["set-compartment", DEV])
    assert result.exit_code == 0, result.output
    saved = json.loads((env / "settings.json").read_text())
    assert saved["oci"]["compartment"] == {"P1": DEV}


# --- final review fix wave ----------------------------------------------------

import time  # noqa: E402

from cloud_login.models import AuthState, Identity, ProviderStatus  # noqa: E402
from cloud_login.providers.oci import OCIProvider  # noqa: E402

API_CFG = f"[API]\nuser={U}\ntenancy={T}\nfingerprint=aa:bb\nkey_file=/k.pem\nregion=eu-frankfurt-1\n"
AUTH_API = ["oci", "session", "authenticate", "--profile-name", "API", "--region", "eu-frankfurt-1"]


def _session_cfg(env, name="SESS"):
    import base64

    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    tok = env / "token"
    tok.write_text(f"{b({'alg': 'RS256'})}.{b({'exp': int(time.time()) + 3600})}.c2ln")
    (env / "config").write_text(f"[{name}]\ntenancy={T}\nregion=us-ashburn-1\nsecurity_token_file={tok}\n")


def test_cli_session_login_refuses_api_key_profile(env, fake_run):
    (env / "config").write_text(API_CFG)
    result = runner.invoke(app, ["login", "oci", "-m", "session", "--profile", "API"])
    assert result.exit_code == 1
    assert "--overwrite" in result.output and "API-key" in result.output
    assert fake_run.calls == []


def test_cli_session_login_overwrite_flag_converts(env, fake_run):
    (env / "config").write_text(API_CFG)
    fake_run.add(AUTH_API, rc=0)
    result = runner.invoke(app, ["login", "oci", "-m", "session", "--profile", "API", "--overwrite"])
    assert result.exit_code == 0, result.output
    assert fake_run.streamed == [AUTH_API]


def test_cli_logout_oci_prompts_and_cancel_runs_nothing(env, fake_run):
    _session_cfg(env)
    result = runner.invoke(app, ["logout", "oci"], input="n\n")
    assert result.exit_code == 1
    assert "REMOVED" in result.output
    assert fake_run.calls == []


def test_cli_logout_oci_confirm_and_yes_flag(env, fake_run):
    _session_cfg(env)
    term = ["oci", "session", "terminate", "--profile", "SESS"]
    fake_run.add(term, rc=0)
    result = runner.invoke(app, ["logout", "oci"], input="y\n")
    assert result.exit_code == 0, result.output
    assert fake_run.calls == [term] and "removed" in result.output
    fake_run.calls.clear()
    result = runner.invoke(app, ["logout", "oci", "--yes"])
    assert result.exit_code == 0, result.output
    assert fake_run.calls == [term] and "Continue?" not in result.output


def test_cli_logout_oci_api_key_profile_not_supported(env, fake_run):
    (env / "config").write_text(API_CFG)
    result = runner.invoke(app, ["logout", "oci"])
    assert result.exit_code == 1 and "API key" in result.output
    assert fake_run.calls == []


def test_cli_status_escapes_oci_values(env, fake_run, monkeypatch):
    st = ProviderStatus(
        "oci",
        AuthState.ERROR,
        identity=Identity(account="[red]t[/red]", user="[b]u[/b]"),
        active_profile="[i]p[/i]",
        message="[bold]m[/bold]",
    )
    monkeypatch.setattr(OCIProvider, "status", lambda self, profile=None: st)
    result = runner.invoke(app, ["status", "-p", "oci"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    for literal in ("[red]t[/red]", "[b]u[/b]", "[i]p[/i]", "[bold]m[/bold]"):
        assert literal in result.output


def test_cli_compartments_and_set_compartment_with_profile(env, fake_run):
    (env / "config").write_text(f"[P1]\ntenancy={T}\n[P2]\ntenancy={T}\n")
    fake_run.add(
        [
            "oci", "iam", "compartment", "list", "--compartment-id", T, "--compartment-id-in-subtree", "true",
            "--all", "--lifecycle-state", "ACTIVE", "--output", "json", "--profile", "P2",
        ],
        out=json.dumps({"data": [{"id": DEV, "name": "dev", "compartment-id": T}]}),
    )  # fmt: skip
    result = runner.invoke(app, ["compartments", "--profile", "P2"])
    assert result.exit_code == 0, result.output
    assert "root/dev" in result.output
    result = runner.invoke(app, ["set-compartment", DEV, "--profile", "P2"])
    assert result.exit_code == 0, result.output
    assert json.loads((env / "settings.json").read_text())["oci"]["compartment"] == {"P2": DEV}
    result = runner.invoke(app, ["set-compartment", DEV, "--profile", "NOPE"])
    assert result.exit_code == 1


def test_cli_login_profile_method_records_profile(env, fake_run):
    (env / "config").write_text(API_CFG + f"\n[P2]\nuser={U}\ntenancy={T}\nfingerprint=aa\nkey_file=/k\n")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "P2"], rc=0)
    result = runner.invoke(app, ["login", "oci", "-m", "profile", "--profile", "P2"])
    assert result.exit_code == 0, result.output
    assert settings.get("last_profile.oci") == "P2"


def test_cli_generate_collision_message_uses_cli_wording(env, fake_run):
    oci_config.generate_key_pair(env / "keys", "ci")
    result = runner.invoke(
        app,
        ["login", "oci", "-m", "api-key", "--profile", "ci", "--user", U, "--tenancy", T,
         "--region", "us-ashburn-1", "--generate-key"],
    )  # fmt: skip
    assert result.exit_code == 1
    assert "--key-file" in result.output and "key_file=" not in result.output


def test_cli_profile_help_mentions_oci():
    result = runner.invoke(app, ["login", "--help"], env={"COLUMNS": "200"})
    assert "OCI" in result.output.split("--profile", 1)[1].split("\n", 1)[0]
