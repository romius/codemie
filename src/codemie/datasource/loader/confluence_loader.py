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

from typing import Any, List, Dict, Optional, Iterator

from langchain_community.document_loaders import ConfluenceLoader
from langchain_core.documents import Document

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.configs import logger


class ConfluenceDatasourceLoader(ConfluenceLoader, BaseDatasourceLoader):
    def fetch_remote_stats(self) -> dict[str, Any]:
        response = self.confluence.cql(self.cql, start=0, limit=1)
        if not isinstance(response, dict):
            raise ValueError("Cannot retrieve data with provided configuration")
        pages_count = response['totalSize']
        total_documents = pages_count  # No extra logic for now
        return {
            self.DOCUMENTS_COUNT_KEY: pages_count,
            self.TOTAL_DOCUMENTS_KEY: total_documents,
            self.SKIPPED_DOCUMENTS_KEY: total_documents - pages_count,
        }

    def _search_content_by_cql(
        self,
        cql: str,
        include_archived_spaces: Optional[bool] = None,
        next_url: str = "",
        **kwargs: Any,
    ) -> tuple[List[dict], str]:
        """Overriden to fix the bug.
        See https://github.com/langchain-ai/langchain/commit/0d20c314dd0508ea956482fbdd6ce7854b85fc01
        (!) IMPORTANT. Remove once underlying langchain_community is updated
        """
        if next_url:
            response = self.confluence.get(next_url)
        else:
            url = "rest/api/content/search"

            params: Dict[str, Any] = {"cql": cql}
            params.update(kwargs)
            if include_archived_spaces is not None:
                params["includeArchivedSpaces"] = include_archived_spaces

            response = self.confluence.get(url, params=params)

        return response.get("results", []), response.get("_links", {}).get("next", "")

    def lazy_load(self) -> Iterator[Document]:
        """Stream the CQL result set, following the `_links.next` URL Confluence returns.

        Yields:
            Document: one per page produced by `process_pages`.
        """
        expand = ",".join(
            [
                self.content_format.value,
                "version",
                *(["metadata.labels"] if self.include_labels else []),
            ]
        )
        next_url = ""
        loaded = 0

        while True:
            pages, next_url = self._search_content_by_cql(
                cql=self.cql,
                include_archived_spaces=self.include_archived_content,
                next_url=next_url,
                limit=self.limit,
                expand=expand,
            )

            if not pages:
                logger.info(f"Confluence loader: empty response, total pages loaded={loaded}")
                break

            yield from self.process_pages(
                pages,
                include_restricted_content=self.include_restricted_content,
                include_attachments=self.include_attachments,
                include_comments=self.include_comments,
                include_labels=self.include_labels,
                content_format=self.content_format,
                ocr_languages=self.ocr_languages,
                keep_markdown_format=self.keep_markdown_format,
                keep_newlines=self.keep_newlines,
            )
            loaded += len(pages)

            if not next_url:
                logger.info(f"Confluence loader: no next link, total pages loaded={loaded}")
                break
