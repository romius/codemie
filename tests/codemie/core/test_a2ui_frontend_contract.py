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

"""Contract test between this backend and the frontend A2UI renderer registry.

Invariant: the backend must never advertise to the LLM a component the frontend
cannot render, and both sides must agree on the catalog id.

The frontend repository publishes a committed snapshot of its renderer registry
at ``src/a2ui/a2ui-manifest.json``. That file is vendored into this repository
at ``src/codemie/core/a2ui/a2ui-manifest.json`` so the invariant is checked
in the backend CI too, where the frontend repository is not checked out.

The vendored copy is maintained BY HAND: whenever the frontend renderer registry
changes, copy ``codemie-ui/src/a2ui/a2ui-manifest.json`` over the vendored file.
Developers with both repositories checked out side by side get that drift
detected automatically by ``test_vendored_copy_matches_frontend_repository``
below; elsewhere the check skips.
"""

import json
from pathlib import Path

import pytest

from codemie.core.a2ui import catalog

# Deliberately the same file name the frontend generates: the copy is made by hand, and
# an identical name is what makes "is this the same file?" answerable at a glance.
MANIFEST_NAME = "a2ui-manifest.json"

VENDORED_MANIFEST = Path(catalog.__file__).parent / MANIFEST_NAME

# <repo>/tests/codemie/core/<this file>
BACKEND_REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_MANIFEST = BACKEND_REPO_ROOT.parent / "codemie-ui" / "src" / "a2ui" / MANIFEST_NAME


@pytest.fixture(scope="module")
def frontend_manifest() -> dict:
    return json.loads(VENDORED_MANIFEST.read_text(encoding="utf-8"))


class TestBackendMatchesFrontendManifest:
    def test_catalog_id_matches(self, frontend_manifest):
        assert frontend_manifest["catalogId"] == catalog.CATALOG_ID

    def test_every_advertised_component_is_renderable(self, frontend_manifest):
        backend_components = set(catalog.component_names())
        frontend_components = set(frontend_manifest["components"])
        unrenderable = backend_components - frontend_components
        assert not unrenderable, f"backend advertises components the frontend cannot render: {sorted(unrenderable)}"

    def test_every_advertised_property_is_renderable(self, frontend_manifest):
        """Component names are too coarse a contract.

        The catalog reaches the two halves through independently versioned packages
        (a2ui-agent-sdk here, @a2ui/web_core there), so one side can carry a property the
        other has never heard of. A property only the BACKEND knows is the dangerous
        direction: it is described to the model, which duly emits it, and the renderer
        then ignores it — the surface renders, quietly missing what was asked for. The
        opposite (frontend-only) is inert, since the model is only ever told what the
        backend catalog contains.
        """
        frontend_properties = frontend_manifest["componentProperties"]
        unrenderable = {}
        for component in sorted(catalog.component_names()):
            declared = set(frontend_properties.get(component, []))
            # `id` and `component` place and type the component; the renderer consumes
            # them before a schema is involved, so they are not renderer properties.
            advertised = set(catalog.component_properties(component)) - {"id", "component"}
            extra = advertised - declared
            if extra:
                unrenderable[component] = sorted(extra)
        assert not unrenderable, (
            "backend advertises properties the frontend renderer does not implement: "
            f"{unrenderable}. Regenerate the manifest (npm run a2ui:manifest) after the "
            "frontend catches up, or stop advertising them."
        )


class TestVendoredManifestIsFresh:
    def test_vendored_copy_matches_frontend_repository(self):
        if not FRONTEND_MANIFEST.is_file():
            pytest.skip(
                f"frontend repository checkout not found at {FRONTEND_MANIFEST}; "
                "drift detection runs only where both repositories are checked out side by side"
            )
        assert VENDORED_MANIFEST.read_bytes() == FRONTEND_MANIFEST.read_bytes(), (
            "vendored frontend manifest is stale — copy "
            "codemie-ui/src/a2ui/a2ui-manifest.json over "
            "src/codemie/core/a2ui/a2ui-manifest.json"
        )
