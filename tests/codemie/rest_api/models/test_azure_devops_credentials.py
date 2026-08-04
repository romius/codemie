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

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.settings import AzureDevOpsCredentials


class TestAzureDevOpsCredentials:
    def _valid_payload(self, **overrides):
        return {
            "base_url": "https://dev.azure.com",
            "organization": "my-org",
            "access_token": "secret-pat",
            **overrides,
        }

    def test_valid_with_project(self):
        creds = AzureDevOpsCredentials(**self._valid_payload(project="my-project"))
        assert creds.base_url == "https://dev.azure.com"
        assert creds.organization == "my-org"
        assert creds.project == "my-project"
        assert creds.access_token == "secret-pat"

    def test_valid_without_project(self):
        creds = AzureDevOpsCredentials(**self._valid_payload())
        assert creds.project is None

    def test_project_none_explicit(self):
        creds = AzureDevOpsCredentials(**self._valid_payload(project=None))
        assert creds.project is None

    @pytest.mark.parametrize("field", ["base_url", "organization", "access_token"])
    def test_mandatory_fields_reject_empty_string(self, field):
        with pytest.raises(ValidationError) as exc_info:
            AzureDevOpsCredentials(**self._valid_payload(**{field: ""}))
        assert field in str(exc_info.value)

    @pytest.mark.parametrize("field", ["base_url", "organization", "access_token"])
    def test_mandatory_fields_reject_missing(self, field):
        payload = self._valid_payload()
        del payload[field]
        with pytest.raises(ValidationError) as exc_info:
            AzureDevOpsCredentials(**payload)
        assert field in str(exc_info.value)

    def test_field_order(self):
        fields = list(AzureDevOpsCredentials.model_fields.keys())
        assert fields == ["base_url", "organization", "project", "access_token"]
