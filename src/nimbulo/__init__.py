"""nimbulo: manage logins across AWS, GCP, Vultr, and Digital Ocean."""
from __future__ import annotations


def main() -> None:
    from .ui.cli import app

    app()
