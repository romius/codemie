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

"""Unit tests for SharePointPKCEService.populate_credentials_from_flow()."""

import json
from unittest.mock import MagicMock

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.sharepoint_pkce_service import SharePointPKCEService

_STATE = "state-abc"
_RESULT_KEY = f"codemie:sp_pkce:result:{_STATE}"
_USER_ID = "user-1"


def _service(result: dict | None):
    """Build a service whose Redis holds `result` under the flow's result key.

    `populate_credentials_from_flow` consumes atomically with GETDEL, so the fixture
    stubs `getdel` and leaves `get` unset.
    """
    redis = MagicMock()
    redis.getdel.return_value = None if result is None else json.dumps(result).encode()
    encryption = MagicMock()
    encryption.decrypt.side_effect = lambda value: value
    return SharePointPKCEService(redis_client=redis, encryption_service=encryption), redis


def _success_result(**overrides):
    result = {
        "status": "success",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": 1700000000,
        "username": "someone@contoso.com",
        "user_id": _USER_ID,
    }
    result.update(overrides)
    return result


def test_tokens_are_mapped_to_credential_values():
    service, _ = _service(_success_result())

    values = {cred.key: cred.value for cred in service.populate_credentials_from_flow(_STATE, _USER_ID)}

    assert values == {
        "auth_type": "oauth",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": "1700000000",
        "username": "someone@contoso.com",
    }


def test_success_consumes_the_result_atomically():
    """GETDEL, not GET-then-DELETE: two concurrent saves must not both succeed."""
    service, redis = _service(_success_result())

    service.populate_credentials_from_flow(_STATE, _USER_ID)

    redis.getdel.assert_called_once_with(_RESULT_KEY)
    redis.delete.assert_not_called()


def test_concurrent_save_finds_nothing_and_is_rejected():
    """After the first save consumes the payload, a second save sees an empty slot."""
    service, redis = _service(_success_result())

    service.populate_credentials_from_flow(_STATE, _USER_ID)

    redis.getdel.return_value = None
    with pytest.raises(ExtendedHTTPException) as e:
        service.populate_credentials_from_flow(_STATE, _USER_ID)
    assert e.value.code == 400


def test_incomplete_flow_is_rejected():
    service, _ = _service(None)

    with pytest.raises(ExtendedHTTPException) as e:
        service.populate_credentials_from_flow(_STATE, _USER_ID)

    assert e.value.code == 400


def test_result_without_access_token_is_rejected():
    """The underlying Microsoft sign-in failed; retrying the save cannot fix that."""
    service, _ = _service(_success_result(access_token=""))

    with pytest.raises(ExtendedHTTPException) as e:
        service.populate_credentials_from_flow(_STATE, _USER_ID)

    assert e.value.code == 502


def test_another_users_flow_cannot_be_consumed():
    service, _ = _service(_success_result(user_id="someone-else"))

    with pytest.raises(ExtendedHTTPException) as e:
        service.populate_credentials_from_flow(_STATE, _USER_ID)

    assert e.value.code == 403


def test_redis_failure_returns_502_and_never_produces_credentials():
    """A partial read must never turn into a Settings row with garbage tokens."""
    service, redis = _service(_success_result())
    redis.getdel.side_effect = Exception("redis down")

    with pytest.raises(ExtendedHTTPException) as e:
        service.populate_credentials_from_flow(_STATE, _USER_ID)

    assert e.value.code == 502
