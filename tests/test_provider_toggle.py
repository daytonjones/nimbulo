from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cloud_login import providers, settings
from cloud_login.models import AuthState, ProviderStatus
from cloud_login.providers.base import Provider
from cloud_login.ui.cli import app

KEYS = ["aws", "gcp", "azure", "oci", "vultr", "digitalocean"]
runner = CliRunner()


def _keys(**kw):
    return [p.key for p in providers.all_providers(**kw)]


def test_default_lists_all_in_order():
    assert _keys() == KEYS


def test_disable_hides_but_get_works():
    assert providers.set_enabled("vultr", False)[0] is True
    assert _keys() == [k for k in KEYS if k != "vultr"]
    assert _keys(include_disabled=True) == KEYS
    assert providers.get("vultr").key == "vultr"
    assert providers.is_enabled("vultr") is False
    assert providers.is_enabled("aws") is True
    assert providers.disabled_keys() == ["vultr"]


def test_settings_file_shape():
    providers.set_enabled("vultr", False)
    assert json.loads(settings.SETTINGS_FILE.read_text()) == {"providers": {"disabled": ["vultr"]}}


def test_cannot_disable_last_enabled():
    for k in KEYS[:-1]:
        assert providers.set_enabled(k, False)[0] is True
    before = settings.SETTINGS_FILE.read_text()
    ok, msg = providers.set_enabled(KEYS[-1], False)
    assert ok is False and msg
    assert settings.SETTINGS_FILE.read_text() == before
    assert _keys() == [KEYS[-1]]


def test_reenable_restores_order():
    providers.set_enabled("aws", False)
    providers.set_enabled("oci", False)
    providers.set_enabled("aws", True)
    providers.set_enabled("oci", True)
    assert _keys() == KEYS
    assert providers.disabled_keys() == []


def test_unknown_key():
    ok, msg = providers.set_enabled("nope", False)
    assert ok is False and msg
    assert not settings.SETTINGS_FILE.exists()


def test_noop_when_state_matches():
    assert providers.set_enabled("aws", True)[0] is True
    providers.set_enabled("aws", False)
    assert providers.set_enabled("aws", False)[0] is True
    assert providers.disabled_keys() == ["aws"]


@pytest.mark.parametrize("bad", ["vultr", None, 5, {"a": 1}])
def test_malformed_settings_do_not_crash(bad):
    settings.set("providers.disabled", bad)
    assert _keys() == KEYS
    assert providers.disabled_keys() == []


def test_unknown_keys_in_list_ignored():
    settings.set("providers.disabled", ["nope", "oci", 3, "aws"])
    assert providers.disabled_keys() == ["aws", "oci"]
    assert _keys() == [k for k in KEYS if k not in ("aws", "oci")]


def test_all_disabled_by_hand_returns_all():
    settings.set("providers.disabled", list(KEYS))
    assert _keys() == KEYS


# --- CLI ---


def test_cli_providers_lists_six():
    r = runner.invoke(app, ["providers"])
    assert r.exit_code == 0
    for k in KEYS:
        assert k in r.output


def test_cli_disable_then_list():
    r = runner.invoke(app, ["providers", "disable", "oci"])
    assert r.exit_code == 0
    assert providers.disabled_keys() == ["oci"]
    r = runner.invoke(app, ["providers"])
    assert r.exit_code == 0
    oci_line = next(ln for ln in r.output.splitlines() if "oci" in ln)
    assert "no" in oci_line.lower()
    r = runner.invoke(app, ["providers", "enable", "oci"])
    assert r.exit_code == 0
    assert providers.disabled_keys() == []


def test_cli_status_omits_disabled(monkeypatch):
    monkeypatch.setattr(
        Provider, "status",
        lambda self, profile=None: ProviderStatus(provider=self.key, state=AuthState.UNKNOWN),
    )
    runner.invoke(app, ["providers", "disable", "oci"])
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    assert "oci" not in r.output and "aws" in r.output
    r = runner.invoke(app, ["status", "-p", "oci"])
    assert r.exit_code == 0 and "oci" in r.output


def test_cli_unknown_key_exit_2():
    r = runner.invoke(app, ["providers", "disable", "nope"])
    assert r.exit_code == 2
    assert "nope" in r.output


def test_cli_last_provider_exit_1():
    for k in KEYS[:-1]:
        assert runner.invoke(app, ["providers", "disable", k]).exit_code == 0
    r = runner.invoke(app, ["providers", "disable", KEYS[-1]])
    assert r.exit_code == 1
    assert r.output.strip()
