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

"""
Context variable carrying the workflow currently being executed.

Integration resolution happens deep inside the settings-handler chain, far away from the
workflow plumbing, and cannot receive an explicit parameter without touching every
``retrieve_setting`` caller. The workflow id therefore travels in a context variable bound where
the current user is already bound for the workflow's background thread, and is unset everywhere
else so chat and the assistant page keep resolving against the assistant scope only.
"""

from contextvars import ContextVar
from typing import Optional

_current_workflow_id: ContextVar[Optional[str]] = ContextVar("current_workflow_id", default=None)


def set_current_workflow_id(workflow_id: Optional[str]) -> None:
    """Bind the workflow being executed to the current context.

    Clearing is the same call with ``None`` rather than a restore of the previous binding: the
    workflow runs on a pooled thread, so whatever it inherited must be overwritten, not put back.
    """
    _current_workflow_id.set(workflow_id)


def get_current_workflow_id() -> Optional[str]:
    """Return the workflow being executed, or None outside a workflow run."""
    return _current_workflow_id.get()
