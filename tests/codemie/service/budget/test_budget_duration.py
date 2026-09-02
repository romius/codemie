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

from datetime import timedelta

import pytest

from codemie.service.budget.budget_duration import parse_duration


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30d", timedelta(days=30)),
        ("7d", timedelta(days=7)),
        ("12h", timedelta(hours=12)),
        ("45m", timedelta(minutes=45)),
        ("60s", timedelta(seconds=60)),
        ("1d", timedelta(days=1)),
    ],
)
def test_parse_valid(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "30", "d30", "-5d", "1.5h", "1x", "30dh", None])
def test_parse_invalid_returns_none(raw):
    assert parse_duration(raw) is None
