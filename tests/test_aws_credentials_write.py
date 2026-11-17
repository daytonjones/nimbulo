"""AWS credentials/config are written 0600 from creation, atomically."""

import os
import stat

import pytest

from nimbulo.providers import aws
from nimbulo.providers.aws import AWSProvider


@pytest.fixture
def aws_dir(tmp_path, monkeypatch):
    d = tmp_path / ".aws"
    monkeypatch.setattr(aws, "AWS_DIR", d)
    monkeypatch.setattr(aws, "CONFIG_FILE", d / "config")
    monkeypatch.setattr(aws, "CREDENTIALS_FILE", d / "credentials")
    return d


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_access_key_files_are_private_and_correct(aws_dir):
    r = AWSProvider().login(
        method="access-key", profile="p", access_key_id="AKIAX", secret_access_key="SECRET", region="us-west-2"
    )
    assert r.ok
    assert mode(aws_dir / "credentials") == 0o600
    assert mode(aws_dir / "config") == 0o600
    assert "SECRET" in (aws_dir / "credentials").read_text()
    assert "region = us-west-2" in (aws_dir / "config").read_text()
    assert [f.name for f in aws_dir.iterdir()] == ["config", "credentials"]  # no temp leftovers


def test_secret_never_world_readable_during_write(aws_dir, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(mode(src))  # mode of the file holding the secret, before it is published
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    AWSProvider().login(method="access-key", profile="p", access_key_id="A", secret_access_key="S")
    assert seen and all(m == 0o600 for m in seen)


def test_existing_profiles_preserved_and_symlink_kept(aws_dir, tmp_path):
    aws_dir.mkdir(mode=0o700)
    real = tmp_path / "dotfiles-credentials"
    real.write_text("[old]\naws_access_key_id = OLD\n")
    (aws_dir / "credentials").symlink_to(real)
    AWSProvider().login(method="access-key", profile="new", access_key_id="N", secret_access_key="S")
    assert (aws_dir / "credentials").is_symlink()
    text = real.read_text()
    assert "[old]" in text and "[new]" in text


def test_failed_write_leaves_no_temp_file(aws_dir, monkeypatch):
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        AWSProvider().login(method="access-key", profile="p", access_key_id="A", secret_access_key="S")
    assert list(aws_dir.iterdir()) == []
