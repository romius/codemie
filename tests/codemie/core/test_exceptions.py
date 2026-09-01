# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from codemie.core.exceptions import ExtendedHTTPException, InterruptedException


def test_init_extended_http_exception():
    exception = ExtendedHTTPException(
        code=400,
        message="Invalid input",
        details="The 'email' field must be a valid email address.",
        help="Please check the format of your email and try again.",
    )

    assert exception.code == 400
    assert exception.message == "Invalid input"
    assert exception.details == "The 'email' field must be a valid email address."
    assert exception.help == "Please check the format of your email and try again."


def test_interrupted_exception_stores_checkpoint_state():
    exc = InterruptedException(
        message="workflow paused",
        interrupted_state="state_b",
        checkpoint_state={"next": ["state_b"], "messages": []},
    )
    assert exc.message == "workflow paused"
    assert exc.interrupted_state == "state_b"
    assert exc.checkpoint_state == {"next": ["state_b"], "messages": []}


def test_interrupted_exception_checkpoint_state_defaults_to_none():
    exc = InterruptedException(message="workflow paused", interrupted_state="state_b")
    assert exc.checkpoint_state is None
