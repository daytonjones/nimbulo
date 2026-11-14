import base64
import json

import pytest

from cloud_login import settings
from cloud_login.models import AuthState
from cloud_login.providers import oci, oci_config
from cloud_login.providers.oci import OCIProvider

T = "ocid1.tenancy.oc1..root"
DEV = "ocid1.compartment.oc1..dev"
TEAM = "ocid1.compartment.oc1..teama"
PROD = "ocid1.compartment.oc1..prod"


def _cmd(profile="P1", tenancy=T):
    return ["oci", "iam", "compartment", "list", "--compartment-id", tenancy,
            "--compartment-id-in-subtree", "true", "--all", "--lifecycle-state", "ACTIVE",
            "--output", "json", "--profile", profile]


def _item(cid, name, parent):
    return {"id": cid, "name": name, "compartment-id": parent, "lifecycle-state": "ACTIVE"}


LISTING = json.dumps({"data": [_item(TEAM, "team-a", DEV), _item(DEV, "dev", T), _item(PROD, "prod", T)]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", tmp_path / "config")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    oci._compartment_cache.clear()
    (tmp_path / "config").write_text(f"[P1]\ntenancy={T}\nregion=us-ashburn-1\n")
    return tmp_path


def test_list_builds_paths_from_parent_ids(fake_run, env):
    fake_run.add(_cmd(), out=LISTING)
    assert OCIProvider().list_secondary() == [
        (T, "root"), (DEV, "root/dev"), (TEAM, "root/dev/team-a"), (PROD, "root/prod")]


def test_list_includes_tenancy_root(fake_run, env):
    fake_run.add(_cmd(), out=json.dumps({"data": []}))
    assert OCIProvider().list_secondary() == [(T, "root")]


def test_missing_parent_is_top_level_and_cycles_terminate(fake_run, env):
    a, b, orphan = "ocid..a", "ocid..b", "ocid..orphan"
    data = [_item(a, "a", b), _item(b, "b", a), _item(orphan, "orph", "ocid..gone")]
    fake_run.add(_cmd(), out=json.dumps({"data": data}))
    got = dict(OCIProvider().list_secondary())
    assert got[orphan] == "orph"
    assert set(got) == {T, a, b, orphan}


def test_list_cached_within_60s(fake_run, env, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(oci, "_clock", lambda: now[0])
    fake_run.add(_cmd(), out=LISTING)
    p = OCIProvider()
    p.list_secondary()
    now[0] += 59
    p.list_secondary()
    assert len(fake_run.calls) == 1
    now[0] += 2
    p.list_secondary()
    assert len(fake_run.calls) == 2


def test_set_secondary_persists_per_profile(fake_run, env):
    (env / "config").write_text(f"[P1]\ntenancy={T}\n[P2]\ntenancy={T}\n")
    p = OCIProvider()
    settings.set("last_profile.oci", "P1")
    assert p.set_secondary(DEV).ok
    settings.set("last_profile.oci", "P2")
    assert p.set_secondary(PROD).ok
    assert p.active_compartment("P1") == DEV
    assert p.active_compartment("P2") == PROD
    assert p.active_compartment() == PROD
    assert p.active_compartment("nope") is None


def test_stale_saved_compartment_dropped(fake_run, env):
    p = OCIProvider()
    p.set_secondary("ocid1.compartment.oc1..deleted")
    assert p.active_compartment() == "ocid1.compartment.oc1..deleted"
    fake_run.add(_cmd(), out=LISTING)
    p.list_secondary()
    assert p.active_compartment() is None
    assert settings.get("oci.compartment") == {}


def test_valid_saved_compartment_kept(fake_run, env):
    p = OCIProvider()
    p.set_secondary(DEV)
    fake_run.add(_cmd(), out=LISTING)
    p.list_secondary()
    assert p.active_compartment() == DEV


def test_list_failure_returns_empty_and_does_not_raise(fake_run, env):
    p = OCIProvider()
    p.set_secondary(DEV)
    fake_run.add(_cmd(), rc=1, err="boom")
    assert p.list_secondary() == []
    assert p.active_compartment() == DEV  # failure must not drop saved value
    fake_run.add(_cmd(), out="not json")
    assert p.list_secondary() == []
    fake_run.add(_cmd(), out=LISTING)  # failures were not cached
    assert len(p.list_secondary()) == 4


# --- Task 3 hardening ---------------------------------------------------------

def _tok(env, payload_json: str):
    b = base64.urlsafe_b64encode(payload_json.encode()).rstrip(b"=").decode()
    f = env / "tok"
    f.write_text(f"e30.{b}.c2ln")
    return str(f)


@pytest.mark.parametrize("payload", [
    '{"exp": 1e999}', '{"exp": Infinity}', '{"exp": 99999999999999999999}',
    '{"exp": "abc"}', '{"exp": NaN}', '[1, 2]', '"str"', '{"exp": null}', '{"exp": true}'])
def test_token_expiry_malformed_never_raises(env, payload):
    exp, err = oci._token_expiry(_tok(env, payload))
    assert exp is None and "not a valid token" in err


def test_status_malformed_exp_is_unauthenticated(fake_run, env):
    tok = _tok(env, '{"exp": Infinity}')
    (env / "config").write_text(f"[S]\ntenancy={T}\nsecurity_token_file={tok}\nkey_file=k\n")
    st = OCIProvider().status("S")
    assert st.state == AuthState.UNAUTHENTICATED
    assert "not a valid token" in st.message


def test_bare_401_in_output_is_not_auth_failure(fake_run, env):
    (env / "config").write_text(
        f"[A]\nuser=ocid1.user..u\ntenancy={T}\nfingerprint=aa\nkey_file=k\n")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "A"], rc=1,
                 err="ConnectionError: opc-request-id 4012ABC401 ocid1.x..401 unreachable")
    assert OCIProvider().status("A").state == AuthState.ERROR
    fake_run.add(["oci", "iam", "region", "list", "--profile", "A"], rc=1,
                 err='{"status":401, "code": "x"}')
    assert OCIProvider().status("A").state == AuthState.UNAUTHENTICATED


NAMES = ["a.b", "me@example.com", "x.y.z", "plain"]


def _cfg_names(env, names):
    (env / "config").write_text("".join(f"[{n}]\ntenancy={T}\n" for n in names))


def test_dotted_profile_names_round_trip_independently(fake_run, env):
    _cfg_names(env, NAMES)
    p = OCIProvider()
    for i, n in enumerate(NAMES):
        settings.set("last_profile.oci", n)
        assert p.set_secondary(f"ocid..{i}").ok
    for i, n in enumerate(NAMES):
        assert p.active_compartment(n) == f"ocid..{i}"
    assert settings.get("oci.compartment") == {n: f"ocid..{i}" for i, n in enumerate(NAMES)}


def test_stale_removal_of_dotted_profile_keeps_others(fake_run, env):
    _cfg_names(env, NAMES)
    p = OCIProvider()
    for n in ("a.b", "plain"):
        settings.set("last_profile.oci", n)
        p.set_secondary(DEV if n == "plain" else "ocid..gone")
    settings.set("last_profile.oci", "a.b")
    fake_run.add(_cmd("a.b"), out=LISTING)
    p.list_secondary()
    assert p.active_compartment("a.b") is None
    assert p.active_compartment("plain") == DEV


def test_corrupt_stored_value_is_treated_as_empty(fake_run, env):
    settings.set("oci.compartment", "garbage")
    p = OCIProvider()
    assert p.active_compartment("P1") is None
    fake_run.add(_cmd(), out=LISTING)
    assert len(p.list_secondary()) == 4
    assert p.set_secondary(DEV).ok
    assert p.active_compartment("P1") == DEV
