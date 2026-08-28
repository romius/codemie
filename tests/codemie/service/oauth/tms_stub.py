# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A stand-in for the enterprise token vault module.

The enterprise package is an optional extra, so the tool OAuth tests that only exercise *our*
logic install this stub instead of skipping. `test_token_port.py` still asserts against the
real package when it is installed, which keeps the stub's contract honest.
"""

import base64
import hashlib
import hmac
import json
from types import ModuleType

TMS_MODULE_PATH = "codemie_enterprise.mcp_auth"


class TokenNotFound(Exception):
    pass


class ReAuthenticationRequired(Exception):
    pass


class TMSUnavailable(Exception):
    pass


class TMSPersistenceError(Exception):
    pass


class TMSCryptoError(Exception):
    pass


class TMSAuditError(Exception):
    pass


class TokenRefreshError(Exception):
    pass


class OAuth2TokenData:
    """Mirrors the fields tool OAuth relies on, including provider identity."""

    model_fields = {
        "access_token": object(),
        "refresh_token": object(),
        "expires_at": object(),
        "refresh_metadata": object(),
        "provider_username": object(),
        "provider_user_id": object(),
        "provider_metadata": object(),
    }


class OAuth2RefreshMetadata:
    """Mirrors the refresh descriptor the token port hands to the vault."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _stub_b64(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _stub_unb64(payload: str) -> bytes:
    return base64.urlsafe_b64decode(f"{payload}{'=' * (-len(payload) % 4)}")


def encode_signed_state(payload: dict, signing_key: bytes) -> str:
    """Mirror the enterprise HMAC-SHA256 signer over an arbitrary dict."""
    body = _stub_b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(signing_key, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_stub_b64(signature)}"


def decode_and_verify_signed_state(state: str, signing_key: bytes) -> dict:
    parts = state.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid OAuth2 callback state")
    body, sig = parts
    expected = hmac.new(signing_key, body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(_stub_unb64(sig), expected):
        raise ValueError("Invalid OAuth2 callback state")
    parsed = json.loads(_stub_unb64(body).decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Invalid OAuth2 callback state")
    return parsed


class RedisEncryption:
    """In-memory stand-in: reversible encoding + a deterministic signing key."""

    def __init__(self, secret):
        self._secret = (secret or "").encode("utf-8") or b"stub-secret"

    @property
    def signing_key(self) -> bytes:
        return hashlib.sha256(self._secret + b"sig").digest()

    def encrypt(self, payload: bytes) -> bytes:
        return base64.b64encode(payload)

    def decrypt(self, payload):
        raw = payload.encode() if isinstance(payload, str) else payload
        return base64.b64decode(raw)


class PKCEStateData:
    """Mirrors the enterprise model's fields that tool OAuth relies on."""

    def __init__(self, *, code_verifier, user_id, auth_config_id, session_binding_hash=None, context=None, **_extra):
        self.code_verifier = code_verifier
        self.user_id = user_id
        self.auth_config_id = auth_config_id
        self.session_binding_hash = session_binding_hash
        self.context = context or {}

    def model_dump_json(self) -> str:
        return json.dumps(
            {
                "code_verifier": self.code_verifier,
                "user_id": self.user_id,
                "auth_config_id": self.auth_config_id,
                "session_binding_hash": self.session_binding_hash,
                "context": self.context,
            }
        )

    @classmethod
    def model_validate_json(cls, raw):
        return cls(**json.loads(raw))


class RedisPKCEStore:
    """In-memory PKCE store mirroring store / consume / peek."""

    def __init__(self, client=None, crypto=None, namespace="tool_oauth"):
        self._store: dict[str, str] = {}
        self._namespace = namespace

    def store(self, state: str, data: "PKCEStateData") -> None:
        self._store[state] = data.model_dump_json()

    def consume(self, state):
        raw = self._store.pop(state, None)
        return PKCEStateData.model_validate_json(raw) if raw is not None else None

    def peek(self, state):
        raw = self._store.get(state)
        return PKCEStateData.model_validate_json(raw) if raw is not None else None


def build_tms_stub_module() -> ModuleType:
    module = ModuleType(TMS_MODULE_PATH)
    for name in (
        "TokenNotFound",
        "ReAuthenticationRequired",
        "TMSUnavailable",
        "TMSPersistenceError",
        "TMSCryptoError",
        "TMSAuditError",
        "TokenRefreshError",
        "OAuth2TokenData",
        "OAuth2RefreshMetadata",
        "RedisEncryption",
        "PKCEStateData",
        "RedisPKCEStore",
        "encode_signed_state",
        "decode_and_verify_signed_state",
    ):
        setattr(module, name, globals()[name])
    return module


def install_tms_stub(monkeypatch) -> ModuleType:
    """Install the stub as `codemie_enterprise.mcp_auth` for the duration of a test."""
    module = build_tms_stub_module()
    monkeypatch.setitem(__import__("sys").modules, TMS_MODULE_PATH, module)
    return module


def install_tms_stub_for_import() -> None:
    """Install the stub before importing a module that needs the vault at import time.

    Some services bind vault symbols at module scope, so monkeypatch (which runs per test,
    after collection) is too late. This registration therefore outlives the importing test
    file — which is why the stub must expose the vault's whole surface, not just the few
    names one file happens to touch.
    """
    import sys
    from importlib.util import find_spec

    if TMS_MODULE_PATH in sys.modules:
        return
    # Never replace a real, installed enterprise package (e.g. in CI): doing so would poison
    # sys.modules for the whole session and break every later test that imports the genuine
    # codemie_enterprise.mcp_auth. Only stand in when the real package is absent.
    try:
        if find_spec(TMS_MODULE_PATH) is not None:
            return
    except (ImportError, ValueError):
        pass
    sys.modules.setdefault("codemie_enterprise", ModuleType("codemie_enterprise"))
    sys.modules[TMS_MODULE_PATH] = build_tms_stub_module()
