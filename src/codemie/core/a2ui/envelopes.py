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

"""Reading envelopes back: the two places that destructure a wire message.

The adapter assembles envelopes; this is the other direction — the catalog resolving a
validation error's path back to the component it blames, and the intake reading a stored
surface. The kind names themselves live in `config`, so a v1.0 rename has one site.
"""

from typing import Any, Optional

from codemie.core.a2ui.config import UPDATE_COMPONENTS


def components_of(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every component a surface declared, across all of its `updateComponents` messages."""
    components: list[dict[str, Any]] = []
    for envelope in envelopes if isinstance(envelopes, list) else []:
        if not isinstance(envelope, dict):
            continue
        update = envelope.get(UPDATE_COMPONENTS)
        if not isinstance(update, dict):
            continue
        declared = update.get("components")
        if isinstance(declared, list):
            components.extend(component for component in declared if isinstance(component, dict))
    return components


def component_at(envelopes: list[dict[str, Any]], detail_path: str) -> Optional[dict[str, Any]]:
    """Resolve a validation detail path such as `updateComponents.components.3`.

    The validator reports where a message failed; this maps that location back to the
    component so the error can name it instead of quoting a path at the reader.
    """
    parts = detail_path.split(".")
    if len(parts) < 3 or parts[0] != UPDATE_COMPONENTS or parts[1] != "components":
        return None
    try:
        index = int(parts[2])
    except ValueError:
        return None
    for envelope in envelopes if isinstance(envelopes, list) else []:
        if not isinstance(envelope, dict):
            continue
        components = (envelope.get(UPDATE_COMPONENTS) or {}).get("components")
        if isinstance(components, list) and index < len(components):
            return components[index]
    return None
