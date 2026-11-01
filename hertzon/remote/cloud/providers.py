"""The cloud providers Hertzon can create servers at, and their settings (Tools > Options > Cloud).

Settings (QSettings keys): cloud/provider ("hetzner" or "aws"); Hetzner: cloud/hetzner_token,
cloud/location, cloud/server_type; AWS: cloud/aws_key_id, cloud/aws_secret, cloud/aws_region, cloud/aws_type.
"""
from __future__ import annotations

from collections.abc import Callable

from . import aws, hetzner

NAMES = {"hetzner": hetzner.NAME, "aws": aws.NAME}
SHORT = {"hetzner": "Hetzner Cloud", "aws": "AWS"}


def current(get: Callable) -> str:
    key = str(get("cloud/provider", "hetzner") or "hetzner")
    return key if key in NAMES else "hetzner"


def credentials(get: Callable, key: str | None = None) -> tuple[str, ...]:
    key = key or current(get)
    if key == "aws":
        return (str(get("cloud/aws_key_id", "") or "").strip(), str(get("cloud/aws_secret", "") or "").strip())
    return (str(get("cloud/hetzner_token", "") or "").strip(),)


def configured(get: Callable) -> bool:
    return all(credentials(get))


def placement(get: Callable, key: str | None = None) -> tuple[str, str]:
    """(server type, location) of the provider."""
    key = key or current(get)
    if key == "aws":
        return (str(get("cloud/aws_type", aws.DEFAULT_TYPE) or aws.DEFAULT_TYPE),
                str(get("cloud/aws_region", aws.DEFAULT_REGION) or aws.DEFAULT_REGION))
    return (str(get("cloud/server_type", hetzner.DEFAULT_TYPE) or hetzner.DEFAULT_TYPE),
            str(get("cloud/location", hetzner.DEFAULT_LOCATION) or hetzner.DEFAULT_LOCATION))


def make(key: str, creds: tuple[str, ...], location: str = ""):
    """The API client of provider KEY."""
    if key == "aws":
        return aws.AWS(creds[0], creds[1], location or aws.DEFAULT_REGION)
    return hetzner.Hetzner(creds[0])


def from_settings(get: Callable):
    """(provider client, server type, location) of the settings."""
    key = current(get)
    stype, loc = placement(get, key)
    return make(key, credentials(get, key), loc), stype, loc
