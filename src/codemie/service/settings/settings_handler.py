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

from typing import Self, Optional

from codemie.rest_api.models.settings import Settings, SettingsBase, SettingType
from codemie.service.settings.base_settings import SearchFields
from codemie.configs import logger
from codemie.rest_api.security.user_context import get_current_user
from codemie.rest_api.security.workflow_context import get_current_workflow_id
from codemie.service.assistant.assistant_user_mapping_service import assistant_user_mapping_service
from codemie.service.settings.settings_util import (
    search_settings_by_id,
    search_assistant,
    search_assistant_settings,
    user_can_access_setting,
)


class SettingsHandler:
    _next_handler: Self or None = None

    def set_next(self, handler: Self) -> Self:
        self._next_handler = handler
        return self

    def __or__(self, other: 'SettingsHandler') -> 'SettingsHandler':
        """Enable pipe-like syntax with | operator"""
        self.set_next(other)
        return other

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        if self._next_handler:
            return self._next_handler.handle(search_fields, **kwargs)

        return None


def _current_user_can_use_mapped_setting(setting: SettingsBase, assistant) -> bool:
    """Check that the requesting user may still use an integration stored in their mapping.

    Access is granted on save, but an integration can later be deleted, unshared or moved out of
    the user's reach, and a stored mapping must not keep resolving it. Mirrors the MCP path's
    runtime re-check and applies the same rule as the save gate: the assistant's project plus its
    marketplace flag. Fails closed when no user is in context, so a mapping can never surface
    credentials outside the user's access.
    """
    current_user = get_current_user()
    if not current_user:
        logger.warning("No current user in context while applying an integration mapping; skipping it")
        return False

    return user_can_access_setting(setting, current_user, assistant.project, marketplace=bool(assistant.is_global))


class AssistantUserMappingSettingsHandler(SettingsHandler):
    """Search by Assistant -> UserSettings mapping; used when user set settings for markeplace assistants"""

    def handle(self, search_fields: dict, assistant_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        next_handler = lambda: super(self.__class__, self).handle(search_fields, assistant_id=assistant_id, **kwargs)  # noqa:E731

        if not assistant_id:
            return next_handler()

        assistant = search_assistant(assistant_id)

        if not assistant:
            return next_handler()

        assistant_setting = search_assistant_settings(assistant, search_fields, None)

        if assistant.is_global and assistant_setting:
            # if global assistant and assistant has setting - skip
            return next_handler()

        user_id = search_fields[SearchFields.USER_ID]
        workflow_id = get_current_workflow_id()

        # Inside a workflow run the two scopes merge per slot — the assistant-wide selection is the
        # baseline and the workflow-scoped one overrides individual slots — which is exactly what
        # the mapping API and the MCP path resolve, so every channel picks the same integration.
        # Falling back per row instead would hide slots the user selected only assistant-wide.
        # Outside a workflow (chat, assistant page) only the assistant scope may ever be read,
        # otherwise a selection made for one workflow would leak everywhere.
        tools_config = (
            assistant_user_mapping_service.get_effective_tools_config(
                assistant_id=assistant_id, user_id=user_id, workflow_id=workflow_id
            )[0]
            if workflow_id
            else None
        )

        if tools_config is None:
            mapping = assistant_user_mapping_service.get_mapping(assistant_id=assistant_id, user_id=user_id)
            tools_config = mapping.tools_config if mapping else []

        if not tools_config:
            return next_handler()

        for config in tools_config:
            if settings := Settings.get_by_fields(
                {"id": config.integration_id, "credential_type": search_fields[SearchFields.CREDENTIAL_TYPE]}
            ):
                if not _current_user_can_use_mapped_setting(settings, assistant):
                    continue
                return settings

        return next_handler()


class BySettingIDSettingsHandler(SettingsHandler):
    """Search setting directly by setting ID"""

    def handle(self, search_fields: dict, setting_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        if setting_id and (settings := search_settings_by_id(setting_id)):
            expected_type = search_fields.get(SearchFields.CREDENTIAL_TYPE)
            if not expected_type or settings.credential_type == expected_type:
                return settings

        return super().handle(search_fields, setting_id=setting_id, **kwargs)


class GlobalAssistantSettingsHandler(SettingsHandler):
    """If assistant is global - returns assistant settings"""

    def handle(self, search_fields: dict, assistant_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        if assistant_id:
            assistant = search_assistant(assistant_id)
            assistant_setting = search_assistant_settings(assistant, search_fields, None)

            if assistant.is_global and assistant_setting:
                return assistant_setting

        return super().handle(search_fields, assistant_id=assistant_id, **kwargs)


class AssistantSettingsHandler(SettingsHandler):
    """Search non-global assistant settings"""

    def handle(self, search_fields: dict, assistant_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        if assistant_id:
            assistant = search_assistant(assistant_id)
            assistant_setting = search_assistant_settings(assistant, search_fields, None)

            if not assistant_setting:
                return super().handle(search_fields, assistant_id=assistant_id, **kwargs)

            match_by_user = (
                assistant_setting.setting_type == SettingType.USER
                and assistant_setting.user_id == search_fields.get(SearchFields.USER_ID)
            )
            match_by_project = assistant_setting.setting_type == SettingType.PROJECT

            if match_by_user or match_by_project:
                return assistant_setting

        return super().handle(search_fields, assistant_id=assistant_id, **kwargs)


class DefaultSettingsHandler(SettingsHandler):
    """Search by seach_fields and default=True. NOTE: might be lagacy"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        default_search_fields = search_fields.copy()
        default_search_fields[SearchFields.DEFAULT] = True

        if settings := Settings.get_by_fields(default_search_fields):
            return settings

        return super().handle(search_fields, **kwargs)


class UserSettingsHandler(SettingsHandler):
    """Search for user non-global setting"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        user_search_fields = search_fields.copy()
        user_search_fields[SearchFields.SETTING_TYPE] = SettingType.USER.value
        user_search_fields[SearchFields.IS_GLOBAL] = False

        if settings := Settings.get_by_fields(user_search_fields):
            return settings

        return super().handle(search_fields, **kwargs)


class GlobalUserSettingsHandler(SettingsHandler):
    """Search for user global setting"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        if not search_fields.get(SearchFields.USER_ID) and search_fields.get(SearchFields.CREDENTIAL_TYPE):
            return super().handle(search_fields)

        global_search_fields = {
            **search_fields,
            SearchFields.IS_GLOBAL: True,
            SearchFields.SETTING_TYPE: SettingType.USER.value,
        }

        # Remove PROJECT_NAME from global search fields if it exists
        global_search_fields.pop(SearchFields.PROJECT_NAME, None)

        if settings := Settings.get_by_fields(global_search_fields):
            return settings

        return super().handle(search_fields, **kwargs)


class ProjectSettingsHandler(SettingsHandler):
    """Search for project setting"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        project_search_fields = search_fields.copy()
        project_search_fields.pop(SearchFields.USER_ID, None)
        project_search_fields[SearchFields.SETTING_TYPE] = SettingType.PROJECT.value
        settings = Settings.get_by_fields(project_search_fields)

        if settings:
            return settings

        return super().handle(search_fields, **kwargs)


def build_settings_handlers():
    start = AssistantUserMappingSettingsHandler()
    (
        start  # 1. Setting by assistant-usersetting mapping (for global assistant)
        | BySettingIDSettingsHandler()  # 2. By setting_id (if provided)
        | GlobalAssistantSettingsHandler()  # 3. If assistant is global - return assistant setting
        | AssistantSettingsHandler()  # 4. If assistant is not global with match by / user project
        | DefaultSettingsHandler()  # 5. Default setting -> by setting 'default' field (legacy?)
        | UserSettingsHandler()  # 6. By matching user setting (match by user and project)
        | GlobalUserSettingsHandler()  # 7. By matching global user setting (match by user)
        | ProjectSettingsHandler()  # 8. By matching by project
    )

    return start
