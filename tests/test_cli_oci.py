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
