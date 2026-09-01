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

import pytest
from unittest.mock import MagicMock
from fastapi import UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from codemie.configs import config
from codemie.rest_api.models.index import (
    IndexKnowledgeBaseFileTypes,
    IndexKnowledgeBaseFileRequest,
    IndexKnowledgeBaseRequest,
    UpdateKnowledgeBaseFileRequest,
)


class TestIndexKnowledgeBaseFileTypes:
    def test_values(self):
        result = IndexKnowledgeBaseFileTypes.values()
        assert result == [
            'pdf',
            'txt',
            'csv',
            'xml',
            'pptx',
            'docx',
            'xlsx',
            'xlsb',
            'html',
            'epub',
            'ipynb',
            'msg',
            'eml',
            'yaml',
            'yml',
            'json',
            'zip',
            'mp3',
            'jpg',
            'jpeg',
            'png',
            'gif',
            'vsdx',
        ]


class TestIndexKnowledgeBaseFileRequest:
    @pytest.fixture
    def mock_attrs(self):
        return {
            'name': 'test_name',
            'project_name': 'test_project_name',
            'description': 'test_description',
            'project_space_visible': True,
            'csv_separator': ',',
            'csv_start_row': 1,
            'csv_rows_per_document': 1,
            'user': None,
        }

    def test_validate_files_ok(self, mock_attrs):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 1024
        mock_file.filename = 'test_file.txt'

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

    def test_validate_files_count_low(self, mock_attrs):
        with pytest.raises(RequestValidationError):
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=[])

    def test_validate_files_count_high(self, mock_attrs):
        mock_files = [MagicMock(spec=UploadFile) for _ in range(11)]

        with pytest.raises(RequestValidationError):
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_files_count_respects_higher_configured_limit(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_COUNT", 50)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(20)]
        for f in mock_files:
            f.size = 1024
            f.filename = 'test_file.txt'

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_file_too_large(self, mock_attrs):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 1024 * 1024 * 1024 + 1
        mock_file.filename = 'test_file.jpg'

        with pytest.raises(RequestValidationError):
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

    @pytest.mark.parametrize("filename", ["photo.jpg", "photo.jpeg", "photo.png", "photo.gif", "photo.JPG"])
    def test_validate_image_too_large_raises(self, mock_attrs, filename):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 10 * 1024 * 1024 + 1
        mock_file.filename = filename

        with pytest.raises(RequestValidationError) as exc_info:
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

        assert "too large" in str(exc_info.value).lower()

    @pytest.mark.parametrize("filename", ["photo.jpg", "photo.jpeg", "photo.png", "photo.gif"])
    def test_validate_image_within_limit_ok(self, mock_attrs, filename):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 10 * 1024 * 1024
        mock_file.filename = filename

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

    def test_validate_non_image_above_image_limit_ok(self, mock_attrs):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 50 * 1024 * 1024
        mock_file.filename = 'document.pdf'

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

    def test_validate_image_error_message_contains_filename(self, mock_attrs):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 10 * 1024 * 1024 + 1
        mock_file.filename = 'my_photo.png'

        with pytest.raises(RequestValidationError) as exc_info:
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

        assert "my_photo.png" in str(exc_info.value)

    def test_validate_total_size_within_limit_ok(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_TOTAL_SIZE", 2048)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(2)]
        for f in mock_files:
            f.size = 1024
            f.filename = 'test_file.txt'

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_total_size_exceeded_raises(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_TOTAL_SIZE", 2048)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(3)]
        for f in mock_files:
            f.size = 1024
            f.filename = 'test_file.txt'

        with pytest.raises(RequestValidationError) as exc_info:
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

        assert "total upload size" in str(exc_info.value).lower()


class TestUpdateKnowledgeBaseFileRequest:
    @pytest.fixture
    def mock_attrs(self):
        return {
            'name': 'test_name',
            'project_name': 'test_project_name',
        }

    def test_validate_files_count_at_default_limit_ok(self, mock_attrs):
        mock_files = [MagicMock(spec=UploadFile) for _ in range(10)]
        for f in mock_files:
            f.size = 1024

        assert UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_files_count_above_default_limit_raises(self, mock_attrs):
        mock_files = [MagicMock(spec=UploadFile) for _ in range(11)]

        with pytest.raises(RequestValidationError):
            UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_files_count_respects_higher_configured_limit(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_COUNT", 50)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(20)]
        for f in mock_files:
            f.size = 1024

        assert UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_total_size_within_limit_ok(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_TOTAL_SIZE", 2048)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(2)]
        for f in mock_files:
            f.size = 1024

        assert UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_total_size_exceeded_raises(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_TOTAL_SIZE", 2048)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(3)]
        for f in mock_files:
            f.size = 1024

        with pytest.raises(RequestValidationError) as exc_info:
            UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

        assert "total upload size" in str(exc_info.value).lower()


class TestIndexKnowledgeBaseRequest:
    @pytest.fixture
    def valid_base_data(self):
        return {
            'project_name': 'codemie',
            'description': 'description goes here',
            'project_space_visible': True,
        }

    @pytest.mark.parametrize(
        "invalid_name",
        [
            '_test_name',
            '-test_name',
            'test@name',
            'test$name',
            'test name',
        ],
    )
    def test_invalid_names(self, valid_base_data, invalid_name):
        test_data = {**valid_base_data, 'name': invalid_name}
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(**test_data)

    @pytest.mark.parametrize(
        "valid_name",
        [
            'test123',
            'Test123',
            'test-123',
            'test_123',
            'testName',
            'a123456',
        ],
    )
    def test_valid_names(self, valid_base_data, valid_name):
        test_data = {**valid_base_data, 'name': valid_name}
        try:
            IndexKnowledgeBaseRequest(**test_data)
        except ValidationError:
            pytest.fail(f"Validation failed for valid name '{valid_name}'")

    def test_name_min_length(self, valid_base_data):
        test_data = {**valid_base_data, 'name': 'tes'}
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(**test_data)

    def test_name_max_length(self, valid_base_data):
        test_data = {**valid_base_data, 'name': 'a' * 51}
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(**test_data)

    @pytest.mark.parametrize(
        "invalid_description",
        [
            "",
            "a" * 501,
        ],
    )
    def test_invalid_descriptions(self, invalid_description: str) -> None:
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(
                name="test-kb", project_name="project1", description=invalid_description, project_space_visible=False
            )
