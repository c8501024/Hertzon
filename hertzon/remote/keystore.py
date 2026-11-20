"""The secrets of Tools > Options live in the key store of the operating system, not in the settings.

Secrets are the API token and keys of the cloud providers and the token of a paired remote computer
(SECRET_KEYS). They go to Windows Credential Manager, the macOS Keychain or the Secret Service of Linux
through the keyring package, under the service "Hertzon" and the settings key as user name. Older versions
kept them in the settings (on Windows the registry, HKCU\\Software\\Hertzon\\Hertzon); ``migrate`` moves them
into the key store when Hertzon starts and removes them from the settings only after reading them back.

Without a usable key store (keyring missing, a Linux without Secret Service) the settings stay the place
for them, as before, and Tools > Options says so. Values are never printed, logged or put into messages.
"""
from __future__ import annotations

import os
from collections.abc import Callable

SERVICE = "Hertzon"
SECRET_KEYS = ("cloud/hetzner_token", "cloud/aws_secret", "cloud/azure_credentials", "remote/token")

_ok: bool | None = None


def _keyring():
    import keyring
    return keyring


def available() -> bool:
    """A key store of the operating system can be used (not keyring's "fail" or "null" stand-ins)."""
    global _ok
    if _ok is None:
        _ok = False
        if os.environ.get("HERTZON_KEYRING", "1") != "0":
            try:
                kr = _keyring().get_keyring()
                _ok = float(getattr(kr, "priority", 0) or 0) > 0 and \
                    type(kr).__module__ not in ("keyring.backends.fail", "keyring.backends.null")
            except Exception:  # noqa: BLE001
                _ok = False
    return _ok


def reset() -> None:
    """Check the key store again (tests, another key store)."""
    global _ok
    _ok = None


def where() -> str:
    """Where the secrets are kept, for Tools > Options."""
    if not available():
        return "the Hertzon settings (no key store of the operating system was found)"
    name = type(_keyring().get_keyring()).__module__.rsplit(".", 1)[-1]
    return {"Windows": "Windows Credential Manager", "macOS": "the macOS Keychain",
            "SecretService": "the Secret Service (GNOME Keyring / KWallet)",
            "kwallet": "KWallet"}.get(name, f"the key store of the operating system ({name})")


def _settings_value(settings, key: str) -> str:
    v = settings.value(key, "")
    return str(v or "").strip()


def get(settings, key: str) -> str:
    """The secret KEY: from the key store, else (no key store, or not migrated yet) from the settings."""
    if key not in SECRET_KEYS:
        return _settings_value(settings, key)
    val = ""
    if available():
        try:
            val = _keyring().get_password(SERVICE, key) or ""
        except Exception:  # noqa: BLE001
            val = ""
    return val or _settings_value(settings, key)


def put(settings, key: str, value: str) -> bool:
    """Keep the secret KEY; True when it is in the key store (and no longer in the settings)."""
    value = (value or "").strip()
    if key not in SECRET_KEYS:
        settings.setValue(key, value)
        return False
    if available():
        try:
            kr = _keyring()
            if value:
                kr.set_password(SERVICE, key, value)
                stored = kr.get_password(SERVICE, key) == value
            else:
                try:
                    kr.delete_password(SERVICE, key)
                except Exception:  # noqa: BLE001
                    pass                            # there was none
                stored = True
        except Exception:  # noqa: BLE001
            stored = False
        if stored:
            settings.remove(key)
            return True
    settings.setValue(key, value)
    return False


def migrate(settings) -> list[str]:
    """Move the secrets that older versions kept in the settings into the key store; the keys moved."""
    moved = []
    if not available():
        return moved
    for key in SECRET_KEYS:
        if not settings.contains(key):
            continue
        val = _settings_value(settings, key)
        if not val:
            settings.remove(key)
            continue
        if put(settings, key, val):
            moved.append(key)
    return moved


def getter(settings) -> Callable:
    """A settings.value-like function that reads the secrets from the key store (for cloud.providers)."""
    def value(key, default=None, **kw):
        if key in SECRET_KEYS:
            return get(settings, key) or (default if default is not None else "")
        return settings.value(key, default, **kw)
    return value
