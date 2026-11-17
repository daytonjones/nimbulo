from pathlib import Path

SELF = Path(__file__).resolve()  # this file names the test itself, so it is skipped
ROOT = SELF.parent.parent
EXCLUDED = {"docs", ".git", ".superpowers", ".venv", "dist", ".pytest_cache", ".ruff_cache", "__pycache__"}


def test_imports():
    import nimbulo
    from nimbulo.providers import all_providers

    assert nimbulo.main
    assert all_providers()


def test_registry_has_six_providers():
    from nimbulo.providers import all_providers

    assert [p.key for p in all_providers()] == ["aws", "gcp", "azure", "oci", "vultr", "digitalocean"]


def test_assets_exist():
    from nimbulo.providers import all_providers
    from nimbulo.ui.gui import ASSETS_DIR

    assert ASSETS_DIR == ROOT / "src" / "nimbulo" / "assets"
    for p in all_providers():
        assert (ASSETS_DIR / f"{p.key}.svg").is_file(), p.key


def test_no_forbidden_name():
    needle = b"lumi" + b"stry"
    offenders = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if EXCLUDED & set(rel.parts) or not path.is_file() or path == SELF:
            continue
        if needle in path.read_bytes().lower() or needle in str(rel).lower().encode():
            offenders.append(str(rel))
    assert not offenders, offenders
