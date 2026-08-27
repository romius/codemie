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

from unittest.mock import MagicMock, patch

import pytest

from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader


@pytest.fixture
def mock_confluence_client():
    """Mock Confluence API client."""
    mock_client = MagicMock()
    return mock_client


@pytest.fixture
def confluence_loader(mock_confluence_client):
    """Create a ConfluenceDatasourceLoader instance with mocked Confluence client."""
    with patch('langchain_community.document_loaders.ConfluenceLoader.__init__', return_value=None):
        loader = ConfluenceDatasourceLoader(
            url="https://confluence.example.com",
            username="test_user",
            api_key="test_api_key",
        )
        loader.confluence = mock_confluence_client
        loader.cql = "type=page AND space=TEST"
        loader.number_of_retries = 3
        loader.min_retry_seconds = 1
        loader.max_retry_seconds = 5
        return loader


class TestFetchRemoteStats:
    """Tests for fetch_remote_stats method."""

    def test_fetch_remote_stats_success(self, confluence_loader, mock_confluence_client):
        """Test successful fetching of remote statistics."""
        # Arrange
        mock_response = {
            'totalSize': 42,
            'results': [],
            '_links': {},
        }
        mock_confluence_client.cql.return_value = mock_response

        # Act
        result = confluence_loader.fetch_remote_stats()

        # Assert
        assert result['documents_count_key'] == 42
        assert result['total_documents'] == 42
        assert result['skipped_documents'] == 0
        mock_confluence_client.cql.assert_called_once_with("type=page AND space=TEST", start=0, limit=1)

    def test_fetch_remote_stats_with_zero_pages(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats when no pages are found."""
        # Arrange
        mock_response = {
            'totalSize': 0,
            'results': [],
            '_links': {},
        }
        mock_confluence_client.cql.return_value = mock_response

        # Act
        result = confluence_loader.fetch_remote_stats()

        # Assert
        assert result['documents_count_key'] == 0
        assert result['total_documents'] == 0
        assert result['skipped_documents'] == 0

    def test_fetch_remote_stats_invalid_response_type(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats raises ValueError when response is not a dict."""
        # Arrange
        mock_confluence_client.cql.return_value = "invalid_response"

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot retrieve data with provided configuration"):
            confluence_loader.fetch_remote_stats()

    def test_fetch_remote_stats_none_response(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats raises ValueError when response is None."""
        # Arrange
        mock_confluence_client.cql.return_value = None

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot retrieve data with provided configuration"):
            confluence_loader.fetch_remote_stats()

    def test_fetch_remote_stats_missing_total_size(self, confluence_loader, mock_confluence_client):
        """Test fetch_remote_stats raises KeyError when totalSize is missing."""
        # Arrange
        mock_response = {
            'results': [],
            '_links': {},
        }
        mock_confluence_client.cql.return_value = mock_response

        # Act & Assert
        with pytest.raises(KeyError):
            confluence_loader.fetch_remote_stats()


class TestSearchContentByCql:
    """Tests for _search_content_by_cql method."""

    def test_search_content_by_cql_first_request(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql on the first request (no next_url)."""
        # Arrange
        mock_response = {
            'results': [
                {'id': '1', 'title': 'Page 1'},
                {'id': '2', 'title': 'Page 2'},
            ],
            '_links': {
                'next': '/rest/api/content/search?cql=...&start=2',
            },
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(
            cql="type=page AND space=TEST",
            limit=2,
            start=0,
        )

        # Assert
        assert len(results) == 2
        assert results[0]['id'] == '1'
        assert results[1]['id'] == '2'
        assert next_url == '/rest/api/content/search?cql=...&start=2'
        mock_confluence_client.get.assert_called_once_with(
            "rest/api/content/search",
            params={
                "cql": "type=page AND space=TEST",
                "limit": 2,
                "start": 0,
            },
        )

    def test_search_content_by_cql_with_next_url(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql with next_url parameter."""
        # Arrange
        next_url = "/rest/api/content/search?cql=...&start=2"
        mock_response = {
            'results': [
                {'id': '3', 'title': 'Page 3'},
            ],
            '_links': {},
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, returned_next_url = confluence_loader._search_content_by_cql(
            cql="type=page AND space=TEST",
            next_url=next_url,
        )

        # Assert
        assert len(results) == 1
        assert results[0]['id'] == '3'
        assert returned_next_url == ''
        mock_confluence_client.get.assert_called_once_with(next_url)

    def test_search_content_by_cql_include_archived_spaces(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql with includeArchivedSpaces parameter."""
        # Arrange
        mock_response = {
            'results': [{'id': '1', 'title': 'Archived Page'}],
            '_links': {},
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(
            cql="type=page",
            include_archived_spaces=True,
        )

        # Assert
        assert len(results) == 1
        assert next_url == ''
        mock_confluence_client.get.assert_called_once_with(
            "rest/api/content/search",
            params={
                "cql": "type=page",
                "includeArchivedSpaces": True,
            },
        )

    def test_search_content_by_cql_no_results(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql when no results are returned."""
        # Arrange
        mock_response = {
            'results': [],
            '_links': {},
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(cql="type=page AND space=NONEXISTENT")

        # Assert
        assert results == []
        assert next_url == ''

    def test_search_content_by_cql_missing_links(self, confluence_loader, mock_confluence_client):
        """Test _search_content_by_cql when _links is missing from response."""
        # Arrange
        mock_response = {
            'results': [{'id': '1', 'title': 'Page 1'}],
        }
        mock_confluence_client.get.return_value = mock_response

        # Act
        results, next_url = confluence_loader._search_content_by_cql(cql="type=page")

        # Assert
        assert len(results) == 1
        assert next_url == ''


class TestLazyLoad:
    """Tests for lazy_load — the walk that follows Confluence's `_links.next`."""

    @pytest.fixture
    def loader_for_lazy(self, confluence_loader):
        """Extend the base fixture with attributes required by lazy_load."""
        from langchain_community.document_loaders.confluence import ContentFormat

        confluence_loader.content_format = ContentFormat.VIEW
        confluence_loader.max_pages = 1000
        confluence_loader.limit = 20
        confluence_loader.include_archived_content = False
        confluence_loader.include_restricted_content = False
        confluence_loader.include_attachments = False
        confluence_loader.include_comments = False
        confluence_loader.include_labels = False
        confluence_loader.ocr_languages = None
        confluence_loader.keep_markdown_format = True
        confluence_loader.keep_newlines = False
        return confluence_loader

    @staticmethod
    def _documents_from(pages, **_):
        from langchain_core.documents import Document

        return iter([Document(page_content=page['id']) for page in pages])

    def test_follows_next_link_until_exhausted(self, loader_for_lazy):
        """Each response's next_url is fed back in; the walk ends when it comes back empty."""
        responses = [
            ([{'id': '1'}, {'id': '2'}], "/next?cursor=a"),
            ([{'id': '3'}, {'id': '4'}], "/next?cursor=b"),
            ([{'id': '5'}], ""),
        ]

        with (
            patch.object(loader_for_lazy, '_search_content_by_cql', side_effect=responses) as mock_search,
            patch.object(loader_for_lazy, 'process_pages', side_effect=self._documents_from),
        ):
            result = list(loader_for_lazy.lazy_load())

        assert [doc.page_content for doc in result] == ['1', '2', '3', '4', '5']
        assert [call.kwargs['next_url'] for call in mock_search.call_args_list] == [
            "",
            "/next?cursor=a",
            "/next?cursor=b",
        ]

    def test_never_sends_a_start_offset(self, loader_for_lazy):
        """Confluence Cloud ignores `start` on CQL search, so it must never be sent."""
        with (
            patch.object(loader_for_lazy, '_search_content_by_cql', return_value=([{'id': '1'}], "")) as mock_search,
            patch.object(loader_for_lazy, 'process_pages', side_effect=self._documents_from),
        ):
            list(loader_for_lazy.lazy_load())

        for call in mock_search.call_args_list:
            assert 'start' not in call.kwargs

    def test_loads_more_pages_than_max_pages(self, loader_for_lazy):
        """max_pages no longer bounds the walk: the next link decides when it ends."""
        from itertools import count

        loader_for_lazy.max_pages = 4
        ids = count(1)

        def two_more(**_):
            batch = [{'id': str(next(ids))} for _ in range(2)]
            return (batch, "" if batch[-1]['id'] == '10' else "/next?cursor=more")

        with (
            patch.object(loader_for_lazy, '_search_content_by_cql', side_effect=two_more),
            patch.object(loader_for_lazy, 'process_pages', side_effect=self._documents_from),
        ):
            result = list(loader_for_lazy.lazy_load())

        assert len(result) == 10

    def test_empty_space_yields_nothing(self, loader_for_lazy):
        """An empty first response ends the walk immediately."""
        with patch.object(loader_for_lazy, '_search_content_by_cql', return_value=([], "")) as mock_search:
            result = list(loader_for_lazy.lazy_load())

        assert result == []
        assert mock_search.call_count == 1

    def test_stops_on_empty_response_advertising_more(self, loader_for_lazy):
        """An empty batch ends the walk even when the server still offers a next link."""
        with patch.object(
            loader_for_lazy, '_search_content_by_cql', return_value=([], "/next?cursor=always")
        ) as mock_search:
            result = list(loader_for_lazy.lazy_load())

        assert result == []
        assert mock_search.call_count == 1


class TestLazyLoadIntegration:
    """Integration tests for lazy_load — mocks only self.confluence HTTP client.

    Verifies the full chain: lazy_load -> _search_content_by_cql -> HTTP, including which
    query parameters actually reach Confluence.
    """

    @staticmethod
    def _make_page(page_id: str) -> dict:
        return {
            "id": page_id,
            "title": f"Page {page_id}",
            "body": {"view": {"value": f"<p>Content of page {page_id}</p>"}},
            "version": {"when": "2024-01-01"},
            "_links": {"webui": f"/pages/{page_id}"},
            "status": "current",
        }

    @pytest.fixture
    def loader_integration(self, mock_confluence_client):
        """Loader with only the HTTP client mocked — all other logic runs for real."""
        from langchain_community.document_loaders.confluence import ContentFormat

        with patch('langchain_community.document_loaders.ConfluenceLoader.__init__', return_value=None):
            loader = ConfluenceDatasourceLoader(
                url="https://confluence.example.com",
                username="test_user",
                api_key="test_api_key",
            )
        loader.confluence = mock_confluence_client
        loader.base_url = "https://confluence.example.com"
        loader.cql = "type=page AND space=TEST"
        loader.number_of_retries = 3
        loader.min_retry_seconds = 1
        loader.max_retry_seconds = 5
        loader.content_format = ContentFormat.VIEW
        loader.max_pages = 1000
        loader.limit = 3
        loader.include_archived_content = False
        loader.include_restricted_content = True  # skip is_public_page API call
        loader.include_attachments = False
        loader.include_comments = False
        loader.include_labels = False
        loader.ocr_languages = None
        loader.keep_markdown_format = False
        loader.keep_newlines = False
        return loader

    def test_next_link_is_followed_verbatim(self, loader_integration, mock_confluence_client):
        """The first call carries the CQL params; later calls are the next link alone."""
        mock_confluence_client.get.side_effect = [
            {
                "results": [self._make_page(str(i)) for i in range(3)],
                "_links": {"next": "/rest/api/content/search?cursor=abc"},
            },
            {"results": [self._make_page(str(i)) for i in range(3, 5)], "_links": {}},
        ]

        result = list(loader_integration.lazy_load())

        assert [doc.metadata["title"] for doc in result] == [f"Page {i}" for i in range(5)]
        calls = mock_confluence_client.get.call_args_list
        assert calls[0].args[0] == "rest/api/content/search"
        assert "start" not in calls[0].kwargs["params"]
        assert "next_url" not in calls[0].kwargs["params"]
        assert calls[1].args == ("/rest/api/content/search?cursor=abc",)

    def test_offset_encoded_next_link_is_followed(self, loader_integration, mock_confluence_client):
        """Confluence Server encodes `start` in `_links.next`; that link is followed as given."""
        mock_confluence_client.get.side_effect = [
            {
                "results": [self._make_page(str(i)) for i in range(3)],
                "_links": {"next": "/rest/api/content/search?limit=3&start=3&cql=type%3Dpage"},
            },
            {
                "results": [self._make_page(str(i)) for i in range(3, 6)],
                "_links": {"next": "/rest/api/content/search?limit=3&start=6&cql=type%3Dpage"},
            },
            {"results": [], "_links": {}},
        ]

        result = list(loader_integration.lazy_load())

        assert [doc.metadata["title"] for doc in result] == [f"Page {i}" for i in range(6)]
        calls = mock_confluence_client.get.call_args_list
        assert calls[1].args == ("/rest/api/content/search?limit=3&start=3&cql=type%3Dpage",)
        assert calls[2].args == ("/rest/api/content/search?limit=3&start=6&cql=type%3Dpage",)
