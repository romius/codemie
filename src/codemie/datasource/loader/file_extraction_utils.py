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

"""Shared file extraction utility used by FilesDatasourceLoader and SharePointLoader."""

from __future__ import annotations

import gc
import os
import tempfile


from langchain_community.document_loaders import CSVLoader, UnstructuredPowerPointLoader
from langchain_community.document_loaders.parsers import BaseImageBlobParser
from langchain_core.documents import Document
from langchain_markitdown import (
    AudioLoader,
    EpubLoader,
    HtmlLoader,
    IpynbLoader,
    PlainTextLoader,
    XlsxLoader,
    ZipLoader,
)

from codemie.configs import logger
from codemie.configs.pyroscope_config import pyroscope_profile
from codemie.core.utils import get_file_extension
from codemie.datasource.datasource_file_storage import DatasourceFileStorage
from codemie.datasource.exceptions import UnreadableWorkbookError
from codemie.datasource.loader.binary.image_loader import ImageLoader
from codemie.core.dependecies import get_llm_by_credentials
from codemie.datasource.loader.docx_loader import DocxLoader
from codemie.datasource.loader.eml_loader import EmlLoader
from codemie.datasource.loader.binary.msg_loader import OutlookMsgWithAttachmentsLoader
from codemie.datasource.loader.binary.pdf_plumber_loader import PDFPlumberLoader
from codemie.datasource.loader.vsdx_loader import VsdxLoader
from codemie.datasource.loader.xlsb_loader import XlsbLoader
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes

LOADERS: dict[str, type] = {
    IndexKnowledgeBaseFileTypes.CSV.value: CSVLoader,
    IndexKnowledgeBaseFileTypes.PDF.value: PDFPlumberLoader,
    IndexKnowledgeBaseFileTypes.PPTX.value: UnstructuredPowerPointLoader,
    IndexKnowledgeBaseFileTypes.DOCX.value: DocxLoader,
    IndexKnowledgeBaseFileTypes.XLSX.value: XlsxLoader,
    IndexKnowledgeBaseFileTypes.XLSB.value: XlsbLoader,
    IndexKnowledgeBaseFileTypes.HTML.value: HtmlLoader,
    IndexKnowledgeBaseFileTypes.EPUB.value: EpubLoader,
    IndexKnowledgeBaseFileTypes.IPYNB.value: IpynbLoader,
    IndexKnowledgeBaseFileTypes.MSG.value: OutlookMsgWithAttachmentsLoader,
    IndexKnowledgeBaseFileTypes.EML.value: EmlLoader,
    IndexKnowledgeBaseFileTypes.ZIP.value: ZipLoader,
    IndexKnowledgeBaseFileTypes.AUDIO.value: AudioLoader,
    IndexKnowledgeBaseFileTypes.IMAGE.value: ImageLoader,
    IndexKnowledgeBaseFileTypes.JPEG.value: ImageLoader,
    IndexKnowledgeBaseFileTypes.PNG.value: ImageLoader,
    IndexKnowledgeBaseFileTypes.GIF.value: ImageLoader,
    IndexKnowledgeBaseFileTypes.VSDX.value: VsdxLoader,
}

DEFAULT_LOADER_KWARGS: dict[str, dict] = {
    IndexKnowledgeBaseFileTypes.PDF.value: {
        "mode": "page",
        "images_inner_format": "markdown-img",
        "extract_images": True,
        "extract_tables": "markdown",
    },
    IndexKnowledgeBaseFileTypes.XLSX.value: {"split_by_page": True},
    IndexKnowledgeBaseFileTypes.XLSB.value: {"split_by_page": True},
}


def _build_images_parser(request_uuid: str | None) -> BaseImageBlobParser:
    from codemie.service.llm_service.llm_service import llm_service

    multimodal_llms = llm_service.get_multimodal_llms()
    if multimodal_llms:
        from langchain_community.document_loaders.parsers import LLMImageBlobParser

        llm = get_llm_by_credentials(
            llm_model=multimodal_llms[0],
            streaming=False,
            request_id=request_uuid,
        )
        return LLMImageBlobParser(model=llm)

    from langchain_community.document_loaders.parsers import TesseractBlobParser

    return TesseractBlobParser()


def is_binary_extractable(file_path: str) -> bool:
    """Return True if the file is handled by extract_documents_from_bytes.

    Accepts a file name or path (e.g. 'report.pdf', '/tmp/image.PNG').
    """
    return get_file_extension(file_path) in LOADERS


_EMAIL_EXTENSIONS = {IndexKnowledgeBaseFileTypes.MSG.value, IndexKnowledgeBaseFileTypes.EML.value}


@pyroscope_profile(
    lambda file_bytes, file_name, *a, **kw: {
        "operation": "file_extraction",
        "file_ext": file_name.rsplit(".", 1)[-1] if "." in file_name else "unknown",
    }
)
def extract_documents_from_bytes(
    file_bytes: bytes,
    file_name: str,
    request_uuid: str | None = None,
    csv_separator: str = ",",
    include_email_attachments: bool = True,
    *,
    datasource_id: str,
) -> list[Document]:
    """
    Extract LangChain Documents from raw bytes using the appropriate loader.

    Args:
        file_bytes: Raw file content.
        file_name: Original file name used to determine loader and rewrite metadata.
        request_uuid: Optional request ID for LLM token-usage tracking.
        csv_separator: CSV column delimiter (default ",").
        include_email_attachments: When True, EML/MSG loaders also extract embedded attachments.
        datasource_id: Datasource identifier used to scope file storage for image loaders.

    Returns:
        List of LangChain Document objects.
    """
    file_ext = get_file_extension(file_name)
    documents: list[Document] = []
    loader_class = LOADERS.get(file_ext, PlainTextLoader)
    loader_kwargs: dict = dict(DEFAULT_LOADER_KWARGS.get(file_ext, {}))

    if file_ext == IndexKnowledgeBaseFileTypes.CSV.value:
        loader_kwargs["csv_args"] = {"delimiter": csv_separator}

    if file_ext == IndexKnowledgeBaseFileTypes.PDF.value or file_ext in IndexKnowledgeBaseFileTypes.image_extensions():
        loader_kwargs["images_parser"] = _build_images_parser(request_uuid)

    if file_ext in IndexKnowledgeBaseFileTypes.image_extensions():
        file_repo = FileRepositoryFactory.get_current_repository()
        loader_kwargs["storage"] = DatasourceFileStorage(datasource_id, file_repo)

    if file_ext in _EMAIL_EXTENSIONS:
        loader_kwargs["include_email_attachments"] = include_email_attachments
        loader_kwargs["datasource_id"] = datasource_id

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{file_ext}", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            temp_path = tmp.name

        loader = loader_class(temp_path, **loader_kwargs)
        try:
            for document in loader.lazy_load():
                document.metadata["source"] = file_name
                if "file_path" in document.metadata:
                    document.metadata["file_path"] = file_name
                documents.append(document)
        except UnicodeDecodeError as e:
            logger.warning(
                f"Failed to load file due to encoding error: {file_name}. "
                f"File cannot be decoded with default encoding: {e}",
                exc_info=True,
            )
        except UnreadableWorkbookError:
            # A corrupt/encrypted workbook is a genuine failure, not an unsupported type.
            # Propagate so the file is accounted for as failed (with an actionable message)
            # instead of being silently swallowed and counted as skipped/unsupported.
            logger.error(f"Failed to extract documents from file {file_name}: unreadable workbook", exc_info=True)
            raise
        except ValueError:
            logger.warning(f"Unsupported file type: {file_ext} for file {file_name}", exc_info=True)
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {temp_path}: {e}")
        gc.collect()

    return documents
