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

"""Tool OAuth creds snapshot + result store."""

import base64

from codemie.service.oauth.stores import CREDS_TTL, ToolCredsSnapshot


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.ttls = {}

    def set(self, key, value, ex=None):
        # Mimic redis-py: string values are stored (and returned) as bytes.
        self.kv[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ex

    def get(self, key):
        return self.kv.get(key)

    def getdel(self, key):
        self.ttls.pop(key, None)
        return self.kv.pop(key, None)


class _FakeCrypto:
    """Reversible byte codec standing in for enterprise RedisEncryption."""

    def encrypt(self, payload: bytes) -> bytes:
        return base64.b64encode(payload)

    def decrypt(self, payload):
        raw = payload.encode() if isinstance(payload, str) else payload
        return base64.b64decode(raw)


class _StrEncryption:
    """Reversible str codec standing in for the app EncryptionFactory service."""

    def encrypt(self, value: str) -> str:
        return base64.b64encode(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return base64.b64decode(value.encode()).decode()


def test_creds_snapshot_round_trip_and_single_use():
    redis = _FakeRedis()
    snap = ToolCredsSnapshot(namespace="tool_oauth", redis_client=redis, crypto=_FakeCrypto())

    assert snap.store("st", {"client_id": "cid", "client_secret": "sec"}) is True
    # TTL applied, and the value is not stored in cleartext
    key = next(iter(redis.kv))
    assert redis.ttls[key] == CREDS_TTL
    assert b"sec" not in (redis.kv[key] if isinstance(redis.kv[key], bytes) else redis.kv[key].encode())

    assert snap.consume("st") == {"client_id": "cid", "client_secret": "sec"}
    assert snap.consume("st") is None  # get-and-delete


def test_creds_snapshot_missing_state_returns_none():
    snap = ToolCredsSnapshot(namespace="tool_oauth", redis_client=_FakeRedis(), crypto=_FakeCrypto())
    assert snap.consume("absent") is None
