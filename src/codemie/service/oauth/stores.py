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

"""Redis-backed stores for the tool OAuth flow.

The in-flight PKCE verifier lives in the enterprise ``RedisPKCEStore``; app credentials live in a
separate encrypted snapshot (never in the PKCE/state record); the flow *result* the browser polls
lives in a small result store. Issued tokens are never stored here — they go to enterprise TMS.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from codemie.clients.redis import create_redis_client
from codemie.configs import config, logger

CREDS_TTL = 600  # 10 minutes (matches the PKCE record lifetime)


def _redis_encryption():
    from codemie_enterprise.mcp_auth import RedisEncryption

    return RedisEncryption(config.MCP_AUTH_HMAC_SECRET)


def signing_key() -> bytes:
    """HMAC signing key for tool OAuth state, derived from MCP_AUTH_HMAC_SECRET (shared with MCP)."""
    return _redis_encryption().signing_key


def build_tool_pkce_store(namespace: str):
    """Enterprise RedisPKCEStore for the in-flight PKCE verifier + non-secret flow context."""
    from codemie_enterprise.mcp_auth import RedisPKCEStore

    return RedisPKCEStore(create_redis_client(), _redis_encryption(), namespace=namespace)


class ToolCredsSnapshot:
    """Encrypted, state-keyed snapshot of app credentials, kept out of the PKCE record.

    App secrets are coupled to the flow only for its lifetime and never serialized into the generic
    PKCE state payload.
    """

    def __init__(self, namespace: str, redis_client=None, crypto=None):
        self._redis = redis_client or create_redis_client()
        self._crypto = crypto or _redis_encryption()
        self._namespace = namespace

    def _key(self, state: str) -> str:
        return f"{self._namespace}:creds:{hashlib.sha256(state.encode('utf-8')).hexdigest()}"

    def store(self, state: str, creds: dict) -> bool:
        try:
            encrypted = self._crypto.encrypt(json.dumps(creds).encode("utf-8"))
            self._redis.set(self._key(state), encrypted, ex=CREDS_TTL)
            return True
        except Exception as exc:
            logger.error(f"Tool OAuth: failed to store credential snapshot: {exc}")
            return False

    def consume(self, state: str) -> Optional[dict]:
        raw = self._redis.getdel(self._key(state))
        if raw is None:
            return None
        try:
            return json.loads(self._crypto.decrypt(raw))
        except Exception as exc:
            logger.warning(f"Tool OAuth: unusable credential snapshot for state: {exc}")
            return None
