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

from typing import Any


class NodeSlot:
    """Mutable node placeholder registered at graph-compile time.

    WorkflowPool pre-compiles a CompiledStateGraph with one NodeSlot per node.
    At execution time _inject_user_context() sets the real agent/tool callable
    on each slot. After execution release() calls clear() so the graph can be
    returned to the pool without carrying stale user references.
    """

    def __init__(self, node_id: str) -> None:
        self._node_id = node_id
        self._delegate: Any = None

    def set_delegate(self, delegate: Any) -> None:
        self._delegate = delegate

    def clear(self) -> None:
        self._delegate = None

    def __call__(self, state_schema: Any) -> Any:
        if self._delegate is None:
            raise RuntimeError(
                f"NodeSlot '{self._node_id}' has no delegate — "
                "_inject_user_context() must be called before streaming"
            )
        return self._delegate(state_schema)
