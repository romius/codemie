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

"""The single place this backend names an A2UI version, identity or limit.

The mirror of `codemie-ui/src/a2ui/config.ts`, and deliberately so: the two halves have to
agree about the same handful of facts, and the fastest way to check whether they still do
is to read one file on each side.

The version appears in three unrelated-looking forms, and they are not interchangeable:

  - `A2UI_VERSION` selects which schema assets are loaded out of the SDK package;
  - `WIRE_VERSION` is stamped into every message and accepted back;
  - `CATALOG_ID` carries the spec *family* segment `v0_9`, not the patch version, because
    0.9.1 is a patch of v0_9 and a catalog is identified by its family URL.

Nothing below is fetched over the network. The catalog id is an identifier that happens to
look like a URL: it is compared by string equality and never dereferenced, and the schemas
behind it ship as files inside the pinned SDK package.

This module sits at the bottom of the A2UI package — it imports the SDK and nothing of
ours — so every other module here can depend on it without a cycle.
"""

from functools import lru_cache
from typing import Iterable

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.inference_formats.direct_json import DirectJsonFormat

# --- Versions and identity -------------------------------------------------

#: Version of the schema assets loaded from `a2ui-agent-sdk` (pinned in pyproject.toml).
A2UI_VERSION = "0.9.1"

#: Version stamped into every message this backend emits.
WIRE_VERSION = "v0.9.1"

#: Message versions accepted on the way back. Both, because a client that predates the
#: patch release states the family version and is otherwise perfectly current.
ACCEPTED_ACTION_VERSIONS = ("v0.9", "v0.9.1")


@lru_cache(maxsize=1)
def format_() -> DirectJsonFormat:
    """The SDK's format object for our version — schemas, prompt text and validation."""
    return DirectJsonFormat(
        version=A2UI_VERSION,
        catalogs=[BasicCatalog.get_config(version=A2UI_VERSION)],
    )


@lru_cache(maxsize=1)
def selected_catalog():
    """The one catalog we serve. `None` means "the default": there is only this one."""
    return format_().get_selected_catalog(None)


#: Canonical id of the Basic Catalog, asked of the SDK rather than written out, so a
#: version bump cannot leave a stale literal behind.
CATALOG_ID: str = format_().supported_catalog_ids[0]


def client_supports_catalog(declared: Iterable[str] | None) -> bool:
    """True when the client declared the catalog this backend serves.

    Exact membership, deliberately: an id in another case or with a different version
    segment is a different catalog, and treating it as ours would promise a client
    components it cannot draw. A client that declares several catalogs simply has ours
    among them — there is nothing to select, since this backend serves exactly one.

    Asked in two places (tool registration and the prompt section), which is why it is a
    function rather than an inline check: those two must never disagree.
    """
    return CATALOG_ID in (declared or [])


# --- The wire vocabulary ---------------------------------------------------

# The protocol renames these in v1.0, so nothing else spells them out — a rename has one
# site. A test fails if a literal reappears anywhere else in the package.
CREATE_SURFACE = "createSurface"
UPDATE_COMPONENTS = "updateComponents"
UPDATE_DATA_MODEL = "updateDataModel"
DELETE_SURFACE = "deleteSurface"

#: Every message kind, in the order the schema's `oneOf` declares them.
MESSAGE_KINDS = (CREATE_SURFACE, UPDATE_COMPONENTS, UPDATE_DATA_MODEL, DELETE_SURFACE)

#: Keys an incoming action envelope may carry. Closed: the catalog is resolved from the
#: surface the server stored, so a client cannot name one here.
ACTION_ENVELOPE_KEYS = frozenset({"version", "action"})
ACTION_REQUIRED_FIELDS = {"name": str, "surfaceId": str}
ACTION_OPTIONAL_FIELDS = {"sourceComponentId": str, "timestamp": str, "context": dict}

#: How many times the agent may author a surface in one turn before the turn ends anyway.
#: A rejected surface is handed back to the model with the validator's reason so it can fix
#: its own mistake — the normalizations only cover the failures we have seen before. The cap
#: exists because a model that cannot satisfy the catalog will not discover it by repeating:
#: three attempts is enough for a slip and short enough that the user is not left waiting.
MAX_SURFACE_ATTEMPTS = 3

#: Action given to a Modal's trigger Button when the agent declares none. The catalog makes
#: `action` required on every Button, including one whose only job is to open a dialog, so a
#: trigger without it discards the whole surface. The renderer suppresses a trigger's
#: dispatch and the intake does not accept it as an answer, so this name is never submitted
#: — it exists to satisfy the schema.
MODAL_TRIGGER_ACTION = "openModal"

# --- Delimiters the SDK puts around the schema block -----------------------

# The reference tool-based integration declares a loosely-typed argument and points the
# model at this block from the tool description, so the pointer has to name the delimiters
# that are really there; `catalog.schema_block_is_present` guards that, since a renamed
# marker would strand the model in 40k of JSON.
SCHEMA_BLOCK_START = "---BEGIN A2UI JSON SCHEMA---"
SCHEMA_BLOCK_END = "---END A2UI JSON SCHEMA---"

# --- Limits ----------------------------------------------------------------

# Agent-authored surfaces are untrusted (steerable via prompt injection); their breadth and
# serialized size are bounded BEFORE validation, streaming and persistence, so a hostile
# tree cannot exhaust the validation budget or bloat every later prompt.
MAX_SURFACE_COMPONENTS = 100
MAX_SURFACE_PAYLOAD_BYTES = 65536

#: How deep a `value.path` binding is honoured. Bounds the walk over a submitted data
#: model; both directions use this one number, so a seed cannot be emitted that the intake
#: would then refuse for depth.
MAX_BINDING_DEPTH = 16

# Submitted payloads are replayed into the prompt on every later turn, so their size is
# capped the way the legacy protocol capped it.
MAX_PAYLOAD_BYTES = 65536
MAX_FIELD_VALUE_LEN = 4096

# `action.name` is an identifier the agent authored and the client echoed back — both
# untrusted — so it is capped independently of the surface: without this, a Button
# declaring a 58 kB name would have that text replayed under the trusted
# "[Structured response to surface ...]" framing.
MAX_ACTION_NAME_LEN = 128
