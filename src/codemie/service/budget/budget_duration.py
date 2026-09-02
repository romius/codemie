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

from __future__ import annotations

import re
from datetime import timedelta

_PATTERN = re.compile(r"^(\d+)([smhd])$")
_UNIT_TO_KWARG = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(raw: str | None) -> timedelta | None:
    """Parse ``"30d"`` / ``"12h"`` / ``"45m"`` / ``"60s"`` into a positive timedelta.

    Returns ``None`` when the input is not a strictly positive ``<int><unit>`` string.
    """
    if not isinstance(raw, str):
        return None
    match = _PATTERN.match(raw)
    if not match:
        return None
    value = int(match.group(1))
    if value <= 0:
        return None
    return timedelta(**{_UNIT_TO_KWARG[match.group(2)]: value})
