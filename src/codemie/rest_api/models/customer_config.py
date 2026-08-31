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

"""API models for the dynamic customer configuration endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from codemie.service.customer_config_declarations import FieldType, Markup


class FieldDeclarationResponse(BaseModel):
    name: str
    type: FieldType
    label: str
    description: str | None = None
    required: bool = False
    max_length: int | None = None
    pattern: str | None = None
    pattern_message: str | None = None
    markup: Markup = Markup.PLAIN


class SettingDeclarationResponse(BaseModel):
    component_id: str
    label: str
    description: str | None = None
    overridden: bool
    value: dict[str, Any]
    fields: list[FieldDeclarationResponse]


class SettingUpdateRequest(BaseModel):
    settings: dict[str, Any]


class SettingUpdateResponse(BaseModel):
    component_id: str
    settings: dict[str, Any]
