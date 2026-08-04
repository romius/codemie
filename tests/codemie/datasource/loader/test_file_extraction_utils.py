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

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from codemie.datasource.exceptions import UnreadableWorkbookError
from codemie.datasource.loader.binary.image_loader import ImageLoader
from codemie.datasource.loader.file_extraction_utils import (
    LOADERS,
    extract_documents_from_bytes,
    is_binary_extractable,
)
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes


class TestIsBinaryExtractable(unittest.TestCase):
    # --- supported binary extensions ---

    def test_returns_true_for_pdf(self):
        self.assertTrue(is_binary_extractable("report.pdf"))

    def test_returns_true_for_docx(self):
        self.assertTrue(is_binary_extractable("document.docx"))

    def test_returns_true_for_xlsx(self):
        self.assertTrue(is_binary_extractable("spreadsheet.xlsx"))

    def test_returns_true_for_pptx(self):
        self.assertTrue(is_binary_extractable("slides.pptx"))

    def test_returns_true_for_msg(self):
        self.assertTrue(is_binary_extractable("email.msg"))

    def test_returns_true_for_jpg(self):
        self.assertTrue(is_binary_extractable("photo.jpg"))

    def test_returns_true_for_jpeg(self):
        self.assertTrue(is_binary_extractable("photo.jpeg"))

    def test_returns_true_for_png(self):
        self.assertTrue(is_binary_extractable("screenshot.png"))

    def test_returns_true_for_gif(self):
        self.assertTrue(is_binary_extractable("animation.gif"))

    # --- text / unsupported extensions ---

    def test_returns_false_for_txt(self):
        self.assertFalse(is_binary_extractable("readme.txt"))

    def test_returns_false_for_py(self):
        self.assertFalse(is_binary_extractable("script.py"))

    def test_returns_false_for_js(self):
        self.assertFalse(is_binary_extractable("app.js"))

    def test_returns_false_for_unknown_extension(self):
        self.assertFalse(is_binary_extractable("archive.xyz"))

    # --- case insensitivity ---

    def test_case_insensitive_pdf_uppercase(self):
        self.assertTrue(is_binary_extractable("REPORT.PDF"))

    def test_case_insensitive_png_uppercase(self):
        self.assertTrue(is_binary_extractable("IMAGE.PNG"))

    def test_case_insensitive_docx_mixed(self):
        self.assertTrue(is_binary_extractable("Doc.Docx"))

    # --- full paths ---

    def test_full_path_pdf_returns_true(self):
        self.assertTrue(is_binary_extractable("/some/path/report.pdf"))

    def test_full_path_txt_returns_false(self):
        self.assertFalse(is_binary_extractable("/some/path/readme.txt"))

    def test_full_path_png_returns_true(self):
        self.assertTrue(is_binary_extractable("/var/data/image.png"))


class TestExtractDocumentsFromBytes(unittest.TestCase):
    """Tests for extract_documents_from_bytes — verifies loader selection by file extension."""

    def _make_mock_loader(self, doc: Document) -> MagicMock:
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.return_value = iter([doc])
        return mock_loader_instance

    def test_csv_loader_selected_for_csv_file(self):
        # Arrange — patch LOADERS so the mock class is resolved at call time
        mock_csv_loader_class = MagicMock()
        mock_doc = Document(page_content="col1,col2", metadata={"source": "data.csv"})
        mock_csv_loader_class.return_value = self._make_mock_loader(mock_doc)

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["csv"] = mock_csv_loader_class
        try:
            # Act
            result = extract_documents_from_bytes(b"col1,col2\nval1,val2", "data.csv", datasource_id="")
        finally:
            utils.LOADERS.update(original_loaders)

        # Assert
        mock_csv_loader_class.assert_called_once()
        self.assertEqual(len(result), 1)

    @patch("codemie.datasource.loader.file_extraction_utils._build_images_parser")
    def test_pdf_loader_selected_for_pdf_file(self, mock_build_parser):
        # Arrange — replace LOADERS entry for pdf with a mock loader class
        mock_build_parser.return_value = MagicMock()
        mock_pdf_loader_class = MagicMock()
        mock_doc = Document(page_content="page content", metadata={"source": "report.pdf", "file_path": "/tmp/x.pdf"})
        mock_pdf_loader_class.return_value = self._make_mock_loader(mock_doc)

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["pdf"] = mock_pdf_loader_class
        try:
            # Act
            result = extract_documents_from_bytes(b"%PDF-1.4 content", "report.pdf", datasource_id="")
        finally:
            utils.LOADERS.update(original_loaders)

        # Assert
        mock_pdf_loader_class.assert_called_once()
        # source metadata should be rewritten to the original file_name
        self.assertEqual(result[0].metadata["source"], "report.pdf")

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_plain_text_loader_used_for_unknown_extension(self, mock_plain_loader_class):
        # Arrange — unknown extension not in LOADERS, so PlainTextLoader is the default
        mock_doc = Document(page_content="plain content", metadata={"source": "file.xyz"})
        mock_plain_loader_class.return_value = self._make_mock_loader(mock_doc)

        # Act
        result = extract_documents_from_bytes(b"plain content", "file.xyz", datasource_id="")

        # Assert
        mock_plain_loader_class.assert_called_once()
        self.assertEqual(len(result), 1)

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_source_metadata_rewritten_to_file_name(self, mock_plain_loader_class):
        # Arrange — loader returns a doc whose source comes from the temp path
        mock_doc = Document(page_content="data", metadata={"source": "/tmp/tmpXXX.xyz"})
        mock_plain_loader_class.return_value = self._make_mock_loader(mock_doc)

        # Act
        result = extract_documents_from_bytes(b"data", "file.xyz", datasource_id="")

        # Assert — source must be overwritten with the original file name
        self.assertEqual(result[0].metadata["source"], "file.xyz")

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_returns_empty_list_when_loader_raises_unicode_error(self, mock_plain_loader_class):
        # Arrange
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "reason")
        mock_plain_loader_class.return_value = mock_loader_instance

        # Act
        result = extract_documents_from_bytes(b"\xff\xfe bad bytes", "bad.xyz", datasource_id="")

        # Assert
        self.assertEqual(result, [])

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_returns_empty_list_when_loader_raises_value_error(self, mock_plain_loader_class):
        # Arrange
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.side_effect = ValueError("Unsupported file type")
        mock_plain_loader_class.return_value = mock_loader_instance

        # Act
        result = extract_documents_from_bytes(b"content", "file.xyz", datasource_id="")

        # Assert
        self.assertEqual(result, [])

    def test_unreadable_workbook_error_propagates_not_swallowed(self):
        """A corrupt/unreadable workbook must surface as a typed failure, not be swallowed
        as an 'unsupported file type' empty result (EPMCDME-11738 AC6)."""
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.side_effect = UnreadableWorkbookError("report.xlsb", "boom")

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["xlsb"] = MagicMock(return_value=mock_loader_instance)
        try:
            with self.assertRaises(UnreadableWorkbookError) as ctx:
                extract_documents_from_bytes(b"corrupt", "report.xlsb", datasource_id="")
        finally:
            utils.LOADERS.update(original_loaders)

        # The actionable message must carry the filename.
        self.assertIn("report.xlsb", str(ctx.exception))

    @patch("codemie.datasource.loader.file_extraction_utils._build_images_parser")
    def test_image_extension_uses_image_loader(self, mock_build_parser):
        mock_build_parser.return_value = MagicMock()
        mock_doc = Document(page_content="meta", metadata={"file_path": "/tmp/x.jpg"})
        mock_loader_instance = self._make_mock_loader(mock_doc)

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["jpg"] = MagicMock(return_value=mock_loader_instance)
        try:
            result = extract_documents_from_bytes(b"fake", "photo.jpg", datasource_id="")
        finally:
            utils.LOADERS.update(original_loaders)

        self.assertEqual(result[0].page_content, "meta")

    @patch("codemie.datasource.loader.file_extraction_utils._build_images_parser")
    def test_gif_extension_uses_image_loader(self, mock_build_parser):
        mock_build_parser.return_value = MagicMock()
        mock_doc = Document(page_content="gif meta", metadata={"file_path": "/tmp/x.gif"})
        mock_loader_instance = self._make_mock_loader(mock_doc)

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["gif"] = MagicMock(return_value=mock_loader_instance)
        try:
            result = extract_documents_from_bytes(b"fake", "animation.gif", datasource_id="")
        finally:
            utils.LOADERS.update(original_loaders)

        self.assertEqual(result[0].page_content, "gif meta")

    @patch("codemie.datasource.loader.file_extraction_utils._build_images_parser")
    def test_images_parser_passed_to_image_loader(self, mock_build_parser):
        """ImageLoader must receive the images_parser built for the request."""
        mock_parser = MagicMock()
        mock_build_parser.return_value = mock_parser

        mock_doc = Document(page_content="text", metadata={"file_path": "/tmp/x.png"})
        captured_kwargs: dict = {}

        class CapturePngLoader:
            def __init__(self, _path, **kwargs):
                captured_kwargs.update(kwargs)

            def lazy_load(self):
                return iter([mock_doc])

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["png"] = CapturePngLoader
        try:
            extract_documents_from_bytes(b"fake", "screenshot.png", datasource_id="")
        finally:
            utils.LOADERS.update(original_loaders)

        assert captured_kwargs.get("images_parser") is mock_parser


# ---------------------------------------------------------------------------
# EPMCDME-9373: GIF support + image metadata extraction
# ---------------------------------------------------------------------------


class TestIndexKnowledgeBaseFileTypesGif:
    """Verify the GIF enum member was added to IndexKnowledgeBaseFileTypes."""

    def test_gif_member_exists_in_enum(self):
        assert IndexKnowledgeBaseFileTypes.GIF.value == "gif"

    def test_gif_value_present_in_loaders(self):
        assert "gif" in LOADERS

    def test_gif_loader_is_image_loader(self):
        assert LOADERS["gif"] is ImageLoader

    def test_gif_value_present_in_image_extensions(self):
        assert "gif" in IndexKnowledgeBaseFileTypes.image_extensions()

    def test_is_binary_extractable_returns_true_for_gif(self):
        assert is_binary_extractable("animation.gif") is True

    def test_is_binary_extractable_returns_true_for_uppercase_gif(self):
        assert is_binary_extractable("ANIMATION.GIF") is True


class TestDatasourceIdThreading(unittest.TestCase):
    """Verify datasource_id flows into DatasourceFileStorage injected into ImageLoader."""

    @patch("codemie.datasource.loader.file_extraction_utils.FileRepositoryFactory")
    @patch("codemie.datasource.loader.file_extraction_utils._build_images_parser")
    def test_storage_injected_with_correct_owner_for_image(self, mock_build_parser, mock_factory):
        mock_build_parser.return_value = MagicMock()
        mock_repo = MagicMock()
        mock_factory.get_current_repository.return_value = mock_repo

        captured_kwargs: dict = {}

        class CapturePngLoader:
            def __init__(self, _path, **kwargs):
                captured_kwargs.update(kwargs)

            def lazy_load(self):
                return iter([])

        import codemie.datasource.loader.file_extraction_utils as utils

        original = dict(utils.LOADERS)
        utils.LOADERS["png"] = CapturePngLoader
        try:
            extract_documents_from_bytes(b"fake", "photo.png", datasource_id="test-uuid")
        finally:
            utils.LOADERS.update(original)

        storage = captured_kwargs.get("storage")
        assert storage is not None
        self.assertEqual(storage._owner, "datasource-test-uuid")

    @patch("codemie.datasource.loader.file_extraction_utils.FileRepositoryFactory")
    @patch("codemie.datasource.loader.file_extraction_utils._build_images_parser")
    def test_no_storage_injected_when_datasource_id_empty(self, mock_build_parser, mock_factory):
        mock_build_parser.return_value = MagicMock()
        mock_factory.get_current_repository.return_value = MagicMock()

        captured_kwargs: dict = {}

        class CapturePngLoader:
            def __init__(self, _path, **kwargs):
                captured_kwargs.update(kwargs)

            def lazy_load(self):
                return iter([])

        import codemie.datasource.loader.file_extraction_utils as utils

        original = dict(utils.LOADERS)
        utils.LOADERS["png"] = CapturePngLoader
        try:
            extract_documents_from_bytes(b"fake", "photo.png", datasource_id="")
        finally:
            utils.LOADERS.update(original)

        # Empty datasource_id still creates storage but with prefix-only owner
        storage = captured_kwargs.get("storage")
        assert storage is not None
        self.assertEqual(storage._owner, "datasource-")
