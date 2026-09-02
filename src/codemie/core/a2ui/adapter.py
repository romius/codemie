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
#

"""Assembly of A2UI wire envelopes and their NDJSON chunk framing.

All wire-level names live here so a future protocol-version migration is a
local change. v0.9 semantics: a surface opens with `createSurface` and receives
its component tree via `updateComponents` (inline components are a v1.0 feature).
"""

from typing import Any, Optional

from codemie.core.a2ui.config import (
    CATALOG_ID,
    CREATE_SURFACE,
    UPDATE_COMPONENTS,
    UPDATE_DATA_MODEL,
    WIRE_VERSION,
)


def build_surface_envelopes(
    surface_id: str,
    components: list[dict[str, Any]],
    data_model: Optional[dict[str, Any]] = None,
    catalog_id: str = CATALOG_ID,
) -> list[dict[str, Any]]:
    envelopes: list[dict[str, Any]] = [
        {
            "version": WIRE_VERSION,
            CREATE_SURFACE: {"surfaceId": surface_id, "catalogId": catalog_id},
        },
        {
            "version": WIRE_VERSION,
            UPDATE_COMPONENTS: {"surfaceId": surface_id, "components": components},
        },
    ]
    if data_model is not None:
        envelopes.append(
            {
                "version": WIRE_VERSION,
                UPDATE_DATA_MODEL: {"surfaceId": surface_id, "path": "/", "value": data_model},
            }
        )
    return envelopes
