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

from unittest.mock import patch


class TestWorkflowPoolStartupHook:
    def test_initialize_optional_features_calls_pool_init_when_flags_enabled(self):
        """When features:subWorkflow is enabled and SUBWORKFLOW_POOL_ENABLED is True,
        _initialize_optional_features() must call workflow_pool.initialize()."""
        with (
            patch("codemie.rest_api.main.config") as mock_config,
            patch("codemie.rest_api.main._customer_config") as mock_customer_config,
            patch("codemie.service.workflow_pool.workflow_pool") as mock_pool,
        ):
            mock_config.TOOL_SELECTION_ENABLED = False
            mock_config.PLATFORM_DATASOURCES_SYNC_ENABLED = False
            mock_config.SUBWORKFLOW_POOL_ENABLED = True
            mock_customer_config.is_feature_enabled.return_value = True

            from codemie.rest_api.main import _initialize_optional_features

            _initialize_optional_features()

        mock_pool.initialize.assert_called_once()

    def test_initialize_optional_features_skips_pool_when_sub_workflow_disabled(self):
        """When features:subWorkflow is disabled, pool init is skipped."""
        with (
            patch("codemie.rest_api.main.config") as mock_config,
            patch("codemie.rest_api.main._customer_config") as mock_customer_config,
            patch("codemie.service.workflow_pool.workflow_pool") as mock_pool,
        ):
            mock_config.TOOL_SELECTION_ENABLED = False
            mock_config.PLATFORM_DATASOURCES_SYNC_ENABLED = False
            mock_config.SUBWORKFLOW_POOL_ENABLED = True
            mock_customer_config.is_feature_enabled.return_value = False

            from codemie.rest_api.main import _initialize_optional_features

            _initialize_optional_features()

        mock_pool.initialize.assert_not_called()

    def test_initialize_optional_features_skips_pool_when_pool_disabled(self):
        """When SUBWORKFLOW_POOL_ENABLED=False, pool init is skipped."""
        with (
            patch("codemie.rest_api.main.config") as mock_config,
            patch("codemie.rest_api.main._customer_config") as mock_customer_config,
            patch("codemie.service.workflow_pool.workflow_pool") as mock_pool,
        ):
            mock_config.TOOL_SELECTION_ENABLED = False
            mock_config.PLATFORM_DATASOURCES_SYNC_ENABLED = False
            mock_config.SUBWORKFLOW_POOL_ENABLED = False
            mock_customer_config.is_feature_enabled.return_value = True

            from codemie.rest_api.main import _initialize_optional_features

            _initialize_optional_features()

        mock_pool.initialize.assert_not_called()
