from __future__ import annotations

from .aws import AWSProvider
from .azure import AzureProvider
from .base import Provider
from .digitalocean import DigitalOceanProvider
from .gcp import GCPProvider
from .oci import OCIProvider
from .vultr import VultrProvider


def all_providers() -> list[Provider]:
    return [
        AWSProvider(),
        GCPProvider(),
        AzureProvider(),
        OCIProvider(),
        VultrProvider(),
        DigitalOceanProvider(),
    ]


def get(name: str) -> Provider:
    name = name.lower()
    for p in all_providers():
        if p.key == name:
            return p
    raise KeyError(f"unknown provider: {name}")


__all__ = [
    "Provider",
    "AWSProvider",
    "GCPProvider",
    "AzureProvider",
    "OCIProvider",
    "VultrProvider",
    "DigitalOceanProvider",
    "all_providers",
    "get",
]
