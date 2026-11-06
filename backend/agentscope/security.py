"""Authentication, roles and at-rest encryption of integration credentials."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from . import config

ROLES = {"viewer": 1, "operator": 2, "admin": 3}
SECRET_FIELDS = {"password", "api_key", "token", "secret"}


def _load_or_create(name: str, factory) -> bytes:
    env = os.environ.get(f"AGENTSCOPE_{name.upper()}")
    if env:
        return env.encode()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / f"{name}.key"
    if path.exists():
        return path.read_bytes().strip()
    value = factory()
    path.write_bytes(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


class Vault:
    """Encrypts secret fields of integration configs (Fernet / AES-128-CBC + HMAC)."""

    def __init__(self):
        self._fernet = Fernet(_load_or_create("secret_key", Fernet.generate_key))

    def encrypt_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in cfg.items():
            if k in SECRET_FIELDS and isinstance(v, str) and v and not v.startswith("enc:"):
                out[k] = "enc:" + self._fernet.encrypt(v.encode()).decode()
            else:
                out[k] = v
        return out

    def decrypt_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in cfg.items():
            if k in SECRET_FIELDS and isinstance(v, str) and v.startswith("enc:"):
                try:
                    out[k] = self._fernet.decrypt(v[4:].encode()).decode()
                except InvalidToken:
                    out[k] = ""
            else:
                out[k] = v
        return out

    @staticmethod
    def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
        return {k: ("********" if k in SECRET_FIELDS and v else v) for k, v in cfg.items()}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return hmac.compare_digest(dk, expected)


class Tokens:
    """Minimal HMAC-signed bearer tokens."""

    def __init__(self):
        self._key = _load_or_create("token_key", lambda: secrets.token_urlsafe(48).encode())

    def issue(self, username: str, role: str, ttl: int = config.TOKEN_TTL_SECONDS) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"u": username, "r": role, "exp": int(time.time()) + ttl}).encode()).decode().rstrip("=")
        sig = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def verify(self, token: str) -> dict | None:
        try:
            payload, sig = token.split(".")
            expected = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if data["exp"] < time.time():
                return None
            return data
        except Exception:
            return None


def role_at_least(role: str, needed: str) -> bool:
    return ROLES.get(role, 0) >= ROLES[needed]
