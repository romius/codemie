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

import functools
from typing import Any, Callable

from codemie.core.otel_tracing import propagated_span


def agent_op_span(operation_name: str) -> Callable:
    """Decorator: wrap a method in a propagated OTEL span keyed to the agent's tracing context."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            with propagated_span(
                self._otel_context,
                operation_name,
                {
                    "codemie.agent_name": self.agent_name,
                    "codemie.conversation_id": self.conversation_id or "",
                },
            ):
                return fn(self, *args, **kwargs)

        return wrapper

    return decorator
