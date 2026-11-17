import pytest

from nimbulo import settings
from nimbulo.providers import oci, oci_config
from nimbulo.providers.oci import OCIProvider

UOCID = "ocid1.user.oc1..aaaa"
TOCID = "ocid1.tenancy.oc1..bbbb"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(oci_config, "OCI_CONFIG_PATH", tmp_path / "config")
    monkeypatch.setattr(oci, "OCI_KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    return tmp_path


def _login(env, **kw):
    args = dict(profile="mine", user=UOCID, tenancy=TOCID, region="us-ashburn-1", generate_key=True)
    args.update(kw)
    lines: list[str] = []
    res = OCIProvider().login("api-key", on_output=lines.append, **args)
    return res, lines


def _no_writes(env):
    assert not (env / "config").exists()
    assert not (env / "keys").exists()


def test_rejects_invalid_user_ocid(env, fake_run):
    res, _ = _login(env, user="nope")
    assert not res.ok and "user" in res.message.lower()
    _no_writes(env)


def test_rejects_invalid_profile_and_tenancy(env, fake_run):
    assert not _login(env, profile="a b")[0].ok
    assert not _login(env, profile="")[0].ok
    assert not _login(env, profile="a]")[0].ok
    res, _ = _login(env, tenancy="ocid1.user.oc1..x")
    assert not res.ok and "tenancy" in res.message.lower()
    _no_writes(env)


def test_rejects_unknown_region_with_hint(env, fake_run):
    res, _ = _login(env, region="us-ashburn-2")
    assert not res.ok and "us-ashburn-1" in res.message
    _no_writes(env)


def test_requires_exactly_one_key_source(env, fake_run):
    assert not _login(env, generate_key=False)[0].ok
    assert not _login(env, key_file="x", generate_key=True)[0].ok
    _no_writes(env)


def test_existing_key_file_missing_is_error(env, fake_run):
    res, _ = _login(env, generate_key=False, key_file=str(env / "missing.pem"))
    assert not res.ok and "key file" in res.message.lower()
    _no_writes(env)


def test_generate_key_writes_profile_and_returns_public_key_only(env, fake_run):
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=0, out="{}")
    res, lines = _login(env)
    assert res.ok and res.message == "logged in as profile mine"
    priv = (env / "keys" / "mine.pem").read_text()
    assert "BEGIN PUBLIC KEY" in res.output
    assert "API keys" in res.output
    secret_body = [ln for ln in priv.splitlines() if not ln.startswith("-----")][0]
    for text in [res.output, res.message, *lines, *(" ".join(c) for c in fake_run.calls)]:
        assert "PRIVATE KEY" not in text and secret_body not in text
    cfg = oci_config.read_profiles()["mine"]
    assert cfg["user"] == UOCID and cfg["key_file"] == str(env / "keys" / "mine.pem")
    assert cfg["fingerprint"] in res.output


def test_existing_key_file_fingerprint_matches(env, fake_run):
    _, _, fp = oci_config.generate_key_pair(env / "k", "x")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=0)
    res, _ = _login(env, generate_key=False, key_file=str(env / "k" / "x.pem"))
    assert res.ok
    assert oci_config.read_profiles()["mine"]["fingerprint"] == fp
    assert oci_config.fingerprint_for_key(env / "k" / "x.pem") == fp


def test_existing_profile_requires_overwrite(env, fake_run):
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=0)
    assert _login(env)[0].ok
    res, _ = _login(env)
    assert not res.ok and "overwrite" in res.message.lower()


def test_verifies_after_write(env, fake_run):
    fake_run.add(
        ["oci", "iam", "region", "list", "--profile", "mine"], rc=1, err='{"status": 401, "code": "NotAuthenticated"}'
    )
    res, _ = _login(env)
    assert not res.ok
    fp = oci_config.read_profiles()["mine"]["fingerprint"]  # profile kept
    assert fp in res.message and "console" in res.message.lower()
    assert "BEGIN PUBLIC KEY" in res.output


def test_offline_failure_worded_differently(env, fake_run):
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=124)
    res, _ = _login(env)
    assert not res.ok and "reach" in res.message.lower()
    assert "mine" in oci_config.read_profiles()


def test_api_key_defaults(env):
    p = OCIProvider()
    assert p.api_key_defaults() == {"profile": "DEFAULT", "region": "us-ashburn-1", "key_dir": str(env / "keys")}
    (env / "config").write_text("[DEFAULT]\nregion=eu-paris-1\ntenancy=t\n")
    d = p.api_key_defaults()
    assert d["profile"] == "my-profile" and d["region"] == "eu-paris-1"


def test_relative_key_file_stored_absolute(env, fake_run, monkeypatch):
    _, _, _ = oci_config.generate_key_pair(env / "k", "x")
    monkeypatch.chdir(env)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=0)
    res, _ = _login(env, generate_key=False, key_file="k/x.pem")
    assert res.ok
    stored = oci_config.read_profiles()["mine"]["key_file"]
    assert stored.startswith("/") and stored == str((env / "k" / "x.pem").resolve())


@pytest.mark.parametrize("bad", ["../../x", "a/b", "/tmp/x", "..", ".", "a\\b", "a..b/c"])
def test_rejects_path_unsafe_profile(env, fake_run, bad):
    res, _ = _login(env, profile=bad)
    assert not res.ok and "profile" in res.message.lower()
    _no_writes(env)


@pytest.mark.parametrize("good", ["me@example.com", "a.b"])
def test_accepts_realistic_profile_names(env, fake_run, good):
    fake_run.add(["oci", "iam", "region", "list", "--profile", good], rc=0)
    res, _ = _login(env, profile=good)
    assert res.ok
    assert (env / "keys" / f"{good}.pem").is_file()


def test_failed_profile_write_removes_generated_keys(env, fake_run, monkeypatch):
    real = oci_config.write_api_key_profile
    monkeypatch.setattr(
        oci_config, "write_api_key_profile", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )
    res, _ = _login(env)
    assert not res.ok
    assert list((env / "keys").iterdir()) == []
    monkeypatch.setattr(oci_config, "write_api_key_profile", real)
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=0)
    assert _login(env)[0].ok


def test_user_supplied_key_never_deleted_on_failure(env, fake_run, monkeypatch):
    oci_config.generate_key_pair(env / "k", "x")
    monkeypatch.setattr(
        oci_config, "write_api_key_profile", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )
    res, _ = _login(env, generate_key=False, key_file=str(env / "k" / "x.pem"))
    assert not res.ok
    assert (env / "k" / "x.pem").is_file() and (env / "k" / "x_public.pem").is_file()


def test_generate_with_existing_key_files_guides_user(env, fake_run):
    oci_config.generate_key_pair(env / "keys", "mine")
    before = {p.name: p.read_bytes() for p in (env / "keys").iterdir()}
    res, _ = _login(env, overwrite=True)
    assert not res.ok
    assert str(env / "keys" / "mine.pem") in res.message
    assert "Key file field" in res.message and "--key-file" in res.message and "remove" in res.message.lower()
    assert "key_file=" not in res.message
    assert {p.name: p.read_bytes() for p in (env / "keys").iterdir()} == before
    assert not (env / "config").exists()


# --- final review fix wave ----------------------------------------------------


def test_existing_key_file_result_shows_public_key(env, fake_run):
    _, pub, fp = oci_config.generate_key_pair(env / "k", "x")
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=1, err='{"status": 401}')
    res, _ = _login(env, generate_key=False, key_file=str(env / "k" / "x.pem"))
    assert pub.read_text().strip() in res.output
    assert "PRIVATE KEY" not in res.output and fp in res.output


@pytest.mark.parametrize("given", ["US-Ashburn-1", "  us-ashburn-1 ", "Us-AshBurn-1\t"])
def test_region_is_normalised(env, fake_run, given):
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=0)
    res, _ = _login(env, region=given)
    assert res.ok, res.message
    assert oci_config.read_profiles()["mine"]["region"] == "us-ashburn-1"


def test_verification_failure_shows_service_error_code(env, fake_run):
    err = 'ServiceError:\n{\n    "code": "InternalServerError",\n    "message": "boom",\n    "status": 500\n}\n'
    fake_run.add(["oci", "iam", "region", "list", "--profile", "mine"], rc=1, err=err)
    res, _ = _login(env)
    assert "verification failed (InternalServerError: boom)" in res.message


def test_malformed_config_api_key_login_fails_cleanly(env, fake_run):
    (env / "config").write_text("[A]\n[A]\n")
    res, _ = _login(env)
    assert not res.ok and "could not be parsed" in res.message
    assert not (env / "keys").exists()
