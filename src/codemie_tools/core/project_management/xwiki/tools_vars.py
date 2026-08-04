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

from codemie_tools.base.models import ToolMetadata

from .models import XWikiConfig

# ---------------------------------------------------------------------------
# Wiki tools
# ---------------------------------------------------------------------------

LIST_WIKIS_TOOL = ToolMetadata(
    name="xwiki_list_wikis",
    label="List Wikis",
    description="""List all wikis available in the xWiki instance. Use to discover which wikis exist before
navigating spaces or pages. In most single-wiki deployments the only result is the default "xwiki" wiki;
in multi-wiki (farm) installations multiple wikis may be listed.

Arguments:
- number (int, optional): Maximum number of wikis to return. Default: 50.
- start (int, optional): Pagination offset for retrieving the next page of results. Default: 0.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "wikis" array. Each wiki object includes:
- 'id': Wiki identifier string — used as the 'wiki' argument in all other xWiki tools
- 'name': Display name of the wiki
- 'description': Wiki description (may be empty)
- 'owner': Username of the wiki owner
- 'syntaxes': List of supported content syntaxes (e.g., "xwiki/2.1")
- 'xwikiAbsoluteUrl': Full URL to the wiki home page

Examples:
- List all wikis:
  number: 50, start: 0
  Result: HTTP: GET https://wiki.example.com/rest/wikis -> 200 OK
          {"wikis": [{"id": "xwiki", "name": "xwiki", "owner": "Admin", "syntaxes": ["xwiki/2.1"], ...}]}

- Paginate wikis in a farm:
  number: 10, start: 10  → second page of results
""",
    user_description="""Lists all wikis available in the xWiki instance. Returns wiki identifiers and metadata.
In most deployments there is a single wiki (id "xwiki") — use this tool to confirm the wiki identifier
before navigating spaces or pages.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

GET_WIKI_TOOL = ToolMetadata(
    name="xwiki_get_wiki",
    label="Get Wiki",
    description="""Get details and metadata for a specific xWiki wiki by its identifier. Use when you need
information about a wiki's owner, supported syntaxes, or home page URL.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki" (the default wiki in most installations).
  Use xwiki_list_wikis to discover available wiki identifiers.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains the wiki object with:
- 'id': Unique wiki identifier
- 'name': Display name
- 'description': Wiki description (may be empty)
- 'owner': Username of the wiki owner
- 'syntaxes': List of supported content syntaxes (e.g., "xwiki/2.1", "markdown/1.2")
- 'xwikiAbsoluteUrl': Full URL to the wiki home page

Examples:
- Get the default wiki:
  wiki: "xwiki"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki -> 200 OK
          {"id": "xwiki", "name": "xwiki", "owner": "Admin", "syntaxes": ["xwiki/2.1"], ...}

- Get a named wiki in a multi-wiki setup:
  wiki: "teamwiki"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/teamwiki -> 200 OK
          {"id": "teamwiki", "name": "Team Wiki", "owner": "Admin", ...}
""",
    user_description="""Retrieves details and metadata for a specific xWiki wiki, including its ID, name,
owner, supported content syntaxes, and home page URL.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

# ---------------------------------------------------------------------------
# Space tools
# ---------------------------------------------------------------------------

LIST_SPACES_TOOL = ToolMetadata(
    name="xwiki_list_spaces",
    label="List Spaces",
    description="""List all top-level spaces in an xWiki wiki. Use to navigate the wiki structure and
determine which space contains the content you need before listing or reading pages.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- number (int, optional): Maximum number of spaces to return. Default: 50.
- start (int, optional): Pagination offset. Default: 0.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "spaces" array. Each space object includes:
- 'id': Space identifier (matches the space name used in other tools)
- 'name': Space display name
- 'home': Name of the space's home page (typically "WebHome")
- 'xwikiAbsoluteUrl': Full URL to the space home page

Note: Only top-level spaces are returned. To explore nested spaces, use xwiki_get_space
with a dot-separated space name (e.g., "Main.Sandbox").

Examples:
- List all spaces in the default wiki:
  wiki: "xwiki", number: 50, start: 0
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces -> 200 OK
          {"spaces": [{"id": "Main", "name": "Main", "home": "WebHome", ...},
                      {"id": "Sandbox", "name": "Sandbox", "home": "WebHome", ...}]}

- Paginate spaces:
  number: 10, start: 10  → second page of results
""",
    user_description="""Lists all top-level spaces in an xWiki wiki. Returns space names and URLs.
Use this to discover which space contains the content you need before browsing pages.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

GET_SPACE_TOOL = ToolMetadata(
    name="xwiki_get_space",
    label="Get Space",
    description="""Get details for a specific xWiki space, including its home page reference and metadata.
Supports both top-level and nested spaces using dot-separated names.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name. Use dot-separated notation for nested spaces.
  Examples: "Main" (top-level), "Main.Sandbox" (nested: Sandbox inside Main).

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains the space object with:
- 'id': Space identifier
- 'name': Space display name
- 'home': Home page name (typically "WebHome")
- 'xwikiAbsoluteUrl': Full URL to the space home page

Examples:
- Get a top-level space:
  wiki: "xwiki", space: "Main"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main -> 200 OK
          {"id": "Main", "name": "Main", "home": "WebHome", ...}

- Get a nested space (dot-separated):
  wiki: "xwiki", space: "Main.Sandbox"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/spaces/Sandbox -> 200 OK
          {"id": "Sandbox", "name": "Sandbox", ...}
""",
    user_description="""Retrieves details for a specific xWiki space, including its home page and metadata.
Supports nested spaces using dot-separated names (e.g., "Main.Sandbox" for the Sandbox space
nested inside Main).

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

# ---------------------------------------------------------------------------
# Page tools
# ---------------------------------------------------------------------------

LIST_PAGES_TOOL = ToolMetadata(
    name="xwiki_list_pages",
    label="List Pages",
    description="""List all pages within a specific xWiki space. Use to browse the contents of a known
space. Prefer this over xwiki_list_wiki_pages when the target space is already known.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- number (int, optional): Maximum number of pages to return. Default: 50.
- start (int, optional): Pagination offset. Default: 0.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "pageSummaries" array. Each page summary includes:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'space': Space name the page belongs to
- 'name': Page name — used as the 'page' argument in other tools
- 'title': Human-readable page title
- 'xwikiAbsoluteUrl': Full URL to the page

Examples:
- List pages in the Main space:
  wiki: "xwiki", space: "Main", number: 50, start: 0
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages -> 200 OK
          {"pageSummaries": [{"id": "xwiki:Main.WebHome", "name": "WebHome", "title": "Main", ...},
                              {"id": "xwiki:Main.MyPage", "name": "MyPage", "title": "My Page", ...}]}

- List pages in a nested space:
  wiki: "xwiki", space: "Main.Sandbox"

- Paginate:
  number: 20, start: 20  → second page of results
""",
    user_description="""Lists all pages within a specific xWiki space. Returns page names, titles, and URLs.
Use when you already know which space to browse — for cross-space discovery use xwiki_list_wiki_pages.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

LIST_WIKI_PAGES_TOOL = ToolMetadata(
    name="xwiki_list_wiki_pages",
    label="List Wiki Pages",
    description="""List all pages across an entire xWiki wiki, regardless of space. Use for a full page
inventory or when you need to discover pages without knowing their space.
Prefer xwiki_list_pages when the target space is known — it is faster and returns fewer results.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- number (int, optional): Maximum number of pages to return. Default: 50.
- start (int, optional): Pagination offset. Default: 0.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "pageSummaries" array. Each page summary includes:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'space': Space name the page belongs to
- 'name': Page name — used as the 'page' argument in other tools
- 'title': Human-readable page title
- 'xwikiAbsoluteUrl': Full URL to the page

Examples:
- List first 50 pages across the entire wiki:
  wiki: "xwiki", number: 50, start: 0
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/pages -> 200 OK
          {"pageSummaries": [{"id": "xwiki:Main.WebHome", "space": "Main", "name": "WebHome", ...},
                              {"id": "xwiki:Sandbox.TestPage", "space": "Sandbox", "name": "TestPage", ...}]}

- Paginate:
  number: 50, start: 50  → second page of results
""",
    user_description="""Lists all pages across an entire xWiki wiki, spanning all spaces. Returns page names,
space assignments, and URLs. Use for full-wiki discovery when the target space is unknown.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

LIST_PAGE_CHILDREN_TOOL = ToolMetadata(
    name="xwiki_list_page_children",
    label="List Page Children",
    description="""List the direct child pages of a given xWiki page. Use to navigate a page hierarchy
or discover sub-pages under a known parent.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Parent page name (e.g., "WebHome", "MyParentPage").
- number (int, optional): Maximum number of child pages to return. Default: 50.
- start (int, optional): Pagination offset. Default: 0.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "pageSummaries" array. Each child page summary includes:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'space': Space name
- 'name': Child page name — used as the 'page' argument in other tools
- 'title': Human-readable title
- 'xwikiAbsoluteUrl': Full URL to the page

Examples:
- List children of the WebHome page:
  wiki: "xwiki", space: "Main", page: "WebHome"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome/children -> 200 OK
          {"pageSummaries": [{"id": "xwiki:Main.GettingStarted", "name": "GettingStarted", "title": "Getting Started", ...}]}

- List children in a nested space:
  wiki: "xwiki", space: "Main.Sandbox", page: "Overview"
""",
    user_description="""Lists the direct child pages of a given xWiki page. Use to navigate a page hierarchy
or discover sub-pages under a known parent.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

GET_PAGE_TOOL = ToolMetadata(
    name="xwiki_get_page",
    label="Get Page",
    description="""Retrieve the full content and metadata of an xWiki page. Set is_markdown=true to
receive the content converted from HTML to Markdown for easier downstream processing.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (e.g., "WebHome", "MyPage").
- is_markdown (bool, optional): If true, converts the response HTML to Markdown. Default: false.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body or Markdown>

When is_markdown=false, the JSON body contains the page object with:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'space': Space name
- 'name': Page name
- 'title': Human-readable page title
- 'content': Page content in xWiki syntax
- 'syntax': Content syntax identifier (e.g., "xwiki/2.1")
- 'version': Current version string (e.g., "3.1")
- 'author': Last author username
- 'modified': Last modification timestamp
- 'xwikiAbsoluteUrl': Full URL to the page

When is_markdown=true, the JSON response body is converted to Markdown text.

Examples:
- Get a page in raw xWiki syntax:
  wiki: "xwiki", space: "Main", page: "WebHome", is_markdown: false
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome -> 200 OK
          {"id": "xwiki:Main.WebHome", "title": "Main", "content": "= Hello =\n\nWelcome...", "version": "2.1", ...}

- Get a page converted to Markdown:
  wiki: "xwiki", space: "Documentation", page: "API-Guide", is_markdown: true
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Documentation/pages/API-Guide -> 200 OK
          # API Guide\n\nThis guide describes...
""",
    user_description="""Retrieves the full content and metadata of an xWiki page. Set is_markdown=true to
convert the response to Markdown for easier reading and downstream processing.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

CREATE_PAGE_TOOL = ToolMetadata(
    name="xwiki_create_page",
    label="Create Page",
    description="""Create a new xWiki page. If a page with the same name already exists, use
xwiki_modify_page instead (create requires a title; modify preserves the existing title when omitted).

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name used as the URL identifier (e.g., "MyNewPage").
  Must not contain spaces — use hyphens or CamelCase.
- title (str, required): Human-readable page title displayed in the wiki UI.
- content (str, required): Page content in the syntax specified by the 'syntax' argument.
- syntax (str, optional): Content syntax identifier. Default: "xwiki/2.1".
  Other supported values: "markdown/1.2", "plain/1.0".

Return Format:
Returns an HTTP response string:
  HTTP: PUT <url> -> <status_code> <reason>
  <JSON body>

On success (HTTP 201 Created), the JSON body contains the created page object with:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'title': Page title
- 'content': Page content as stored
- 'syntax': Content syntax
- 'version': Initial version string (e.g., "1.1")
- 'xwikiAbsoluteUrl': Full URL to the new page

Examples:
- Create a page in xWiki syntax:
  wiki: "xwiki", space: "Main", page: "MyNewPage", title: "My New Page",
  content: "= Introduction =\n\nThis is a new page.", syntax: "xwiki/2.1"
  Result: HTTP: PUT https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/MyNewPage -> 201 Created
          {"id": "xwiki:Main.MyNewPage", "title": "My New Page", "version": "1.1", ...}

- Create a Markdown page in a nested space:
  wiki: "xwiki", space: "Main.Sandbox", page: "TestDoc", title: "Test Document",
  content: "# Test\n\nHello world.", syntax: "markdown/1.2"
""",
    user_description="""Creates a new page in xWiki. Requires a page name (URL identifier), a display title,
and the page content. If the page already exists, use xwiki_modify_page to update it instead.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

MODIFY_PAGE_TOOL = ToolMetadata(
    name="xwiki_modify_page",
    label="Modify Page",
    description="""Update the content of an existing xWiki page. Optionally provide a new title to rename
the page at the same time. Use xwiki_create_page for pages that do not yet exist.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- content (str, required): New page content in wiki syntax.
- title (str, optional): New page title. If omitted, the existing title is preserved.
- syntax (str, optional): Content syntax identifier. Default: "xwiki/2.1".
  Other supported values: "markdown/1.2", "plain/1.0".

Return Format:
Returns an HTTP response string:
  HTTP: PUT <url> -> <status_code> <reason>
  <JSON body>

On success (HTTP 200 OK), the JSON body contains the updated page object with:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'title': Page title (updated if provided, otherwise unchanged)
- 'content': Updated content as stored
- 'version': New version string (e.g., "2.1")
- 'modified': Updated modification timestamp
- 'xwikiAbsoluteUrl': Full URL to the page

Examples:
- Update page content, keeping the existing title:
  wiki: "xwiki", space: "Main", page: "MyPage",
  content: "= Updated =\n\nNew content here."
  Result: HTTP: PUT https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/MyPage -> 200 OK
          {"id": "xwiki:Main.MyPage", "title": "My Page", "version": "2.1", ...}

- Update content and rename the page:
  wiki: "xwiki", space: "Main", page: "OldName",
  title: "New Title", content: "Updated content."
""",
    user_description="""Updates the content of an existing xWiki page. Optionally provide a new title to
rename the page. If the page does not exist yet, use xwiki_create_page instead.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

DELETE_PAGE_TOOL = ToolMetadata(
    name="xwiki_delete_page",
    label="Delete Page",
    description="""Permanently delete an xWiki page.

IMPORTANT: This action is irreversible. The page and all its history will be removed.
Confirm with the user before calling this tool.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").

Return Format:
Returns an HTTP response string:
  HTTP: DELETE <url> -> <status_code> <reason>
  <response body (empty on success)>

On success, the response is HTTP 204 No Content with an empty body.
On failure (e.g., page not found), returns HTTP 404 with an error message.

Examples:
- Delete a page:
  wiki: "xwiki", space: "Main", page: "ObsoletePage"
  Result: HTTP: DELETE https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/ObsoletePage -> 204 No Content

- Delete a page in a nested space:
  wiki: "xwiki", space: "Main.Archive", page: "OldDocs"
  Result: HTTP: DELETE https://wiki.example.com/rest/wikis/xwiki/spaces/Main/spaces/Archive/pages/OldDocs -> 204 No Content
""",
    user_description="""Permanently deletes an xWiki page. This action is irreversible — always confirm
with the user before proceeding.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

# ---------------------------------------------------------------------------
# Tag tools
# ---------------------------------------------------------------------------

LIST_WIKI_TAGS_TOOL = ToolMetadata(
    name="xwiki_list_wiki_tags",
    label="List Wiki Tags",
    description="""List all tags used across an xWiki wiki, along with the count of pages using each tag.
Use for tag discovery or to find the correct tag name before filtering pages or managing tags.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "tags" array. Each tag object includes:
- 'name': Tag name string — used as input to xwiki_set_page_tags
- 'count': Number of pages that have this tag applied

Examples:
- List all tags in the wiki:
  wiki: "xwiki"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/tags -> 200 OK
          {"tags": [{"name": "documentation", "count": 12},
                    {"name": "api", "count": 5},
                    {"name": "deprecated", "count": 3}]}
""",
    user_description="""Lists all tags used across an xWiki wiki, with the count of pages using each tag.
Use this to discover available tags before filtering pages or managing page tags.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

LIST_PAGE_TAGS_TOOL = ToolMetadata(
    name="xwiki_list_page_tags",
    label="List Page Tags",
    description="""Get all tags currently applied to a specific xWiki page. Use to inspect current tagging
before deciding whether to add, remove, or replace tags with xwiki_set_page_tags.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "tags" array. Each tag object includes:
- 'name': Tag name string

Examples:
- Get tags on a page:
  wiki: "xwiki", space: "Main", page: "WebHome"
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome/tags -> 200 OK
          {"tags": [{"name": "documentation"}, {"name": "getting-started"}]}
""",
    user_description="""Retrieves all tags currently applied to a specific xWiki page. Use this to inspect
page tags before updating them with xwiki_set_page_tags.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

SET_PAGE_TAGS_TOOL = ToolMetadata(
    name="xwiki_set_page_tags",
    label="Set Page Tags",
    description="""Set (replace) the complete list of tags on an xWiki page.

IMPORTANT: This operation replaces all existing tags. Tags not included in the new list will be
removed. Call xwiki_list_page_tags first if you want to append tags rather than replace them.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- tags (list[str], required): Complete list of tag names to apply.
  Pass an empty list [] to remove all tags.

Return Format:
Returns an HTTP response string:
  HTTP: PUT <url> -> <status_code> <reason>
  <JSON body>

On success (HTTP 200 OK), the JSON body contains the updated tag list:
- 'tags': Array of tag objects with 'name' field, reflecting the new state

Examples:
- Replace all tags on a page:
  wiki: "xwiki", space: "Main", page: "WebHome", tags: ["documentation", "getting-started"]
  Result: HTTP: PUT https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome/tags -> 200 OK
          {"tags": [{"name": "documentation"}, {"name": "getting-started"}]}

- Append a tag (read-then-write pattern):
  Step 1: xwiki_list_page_tags → existing: ["documentation"]
  Step 2: xwiki_set_page_tags, tags: ["documentation", "api"]   (include existing + new)

- Remove all tags from a page:
  wiki: "xwiki", space: "Main", page: "OldPage", tags: []
""",
    user_description="""Sets the complete list of tags on an xWiki page, replacing all existing tags.
To append a tag without removing others, first call xwiki_list_page_tags to get the current list.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

# ---------------------------------------------------------------------------
# Comment tools
# ---------------------------------------------------------------------------

LIST_PAGE_COMMENTS_TOOL = ToolMetadata(
    name="xwiki_list_page_comments",
    label="List Page Comments",
    description="""List all comments on an xWiki page. Returns comment IDs, authors, dates, and text.
Use to read page discussion or to find a comment ID before retrieving a single comment with
xwiki_get_page_comment.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- number (int, optional): Maximum number of comments to return. Default: 20.
- start (int, optional): Pagination offset. Default: 0.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "comments" array. Each comment object includes:
- 'id': Numeric comment ID — used as the 'comment_id' argument in xwiki_get_page_comment
- 'text': Comment text content
- 'author': Username of the comment author
- 'date': Comment creation timestamp
- 'replyTo': ID of the parent comment if this is a reply (may be absent)

Examples:
- List comments on a page:
  wiki: "xwiki", space: "Main", page: "WebHome", number: 20, start: 0
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome/comments -> 200 OK
          {"comments": [{"id": 1, "text": "Great page!", "author": "User1", "date": "2024-01-10T09:00:00"},
                        {"id": 2, "text": "Agreed.", "author": "User2", "date": "2024-01-11T10:30:00"}]}
""",
    user_description="""Lists all comments on an xWiki page, including author, date, and text. Use this to
read page discussions or find comment IDs for retrieving individual comments.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

GET_PAGE_COMMENT_TOOL = ToolMetadata(
    name="xwiki_get_page_comment",
    label="Get Page Comment",
    description="""Get the full content and metadata of a single xWiki page comment by its numeric ID.
Use xwiki_list_page_comments first to find the comment ID.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- comment_id (int, required): Numeric comment ID obtained from xwiki_list_page_comments.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains the comment object with:
- 'id': Numeric comment ID
- 'text': Comment text content
- 'author': Username of the author
- 'date': Comment creation timestamp
- 'replyTo': ID of the parent comment if this is a reply (may be absent)

Examples:
- Get comment with ID 42:
  wiki: "xwiki", space: "Main", page: "WebHome", comment_id: 42
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome/comments/42 -> 200 OK
          {"id": 42, "text": "This needs clarification.", "author": "User1", "date": "2024-01-10T09:00:00"}
""",
    user_description="""Retrieves the full content and metadata of a single xWiki page comment by its
numeric ID. Use xwiki_list_page_comments to find the comment ID first.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

CREATE_PAGE_COMMENT_TOOL = ToolMetadata(
    name="xwiki_create_page_comment",
    label="Create Page Comment",
    description="""Add a new comment to an xWiki page. Use to post feedback, notes, or review comments
directly on a wiki page.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- text (str, required): Comment text content. Plain text or xWiki markup.

Return Format:
Returns an HTTP response string:
  HTTP: POST <url> -> <status_code> <reason>
  <JSON body>

On success (HTTP 201 Created), the JSON body contains the created comment object with:
- 'id': Numeric ID of the new comment — use for future lookups
- 'text': The comment text as stored
- 'author': Username of the comment author
- 'date': Creation timestamp

Examples:
- Add a feedback comment:
  wiki: "xwiki", space: "Documentation", page: "API-Guide",
  text: "The authentication section is missing examples."
  Result: HTTP: POST https://wiki.example.com/rest/.../pages/API-Guide/comments -> 201 Created
          {"id": 15, "text": "The authentication section is missing examples.", "author": "Admin", "date": "2024-01-15T14:00:00"}
""",
    user_description="""Adds a new comment to an xWiki page. Use to post feedback, notes, or review
comments on wiki pages.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

# ---------------------------------------------------------------------------
# Attachment tools
# ---------------------------------------------------------------------------

LIST_PAGE_ATTACHMENTS_TOOL = ToolMetadata(
    name="xwiki_list_page_attachments",
    label="List Page Attachments",
    description="""List all file attachments on an xWiki page. Returns filenames, MIME types, sizes, and
download URLs. Use to discover attached files before reading content with
xwiki_read_attachment_content or deleting with xwiki_delete_page_attachment.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains an "attachments" array. Each attachment object includes:
- 'id': Attachment identifier
- 'name': Attachment filename — used as the 'filename' argument in other attachment tools
- 'mimeType': MIME type of the file (e.g., "application/pdf", "image/png")
- 'fileSize': File size in bytes
- 'author': Username of the uploader
- 'date': Upload timestamp
- 'xwikiAbsoluteUrl': Full URL to download the attachment

Examples:
- List attachments on a documentation page:
  wiki: "xwiki", space: "Documentation", page: "Architecture"
  Result: HTTP: GET https://wiki.example.com/rest/.../pages/Architecture/attachments -> 200 OK
          {"attachments": [{"name": "diagram.pdf", "mimeType": "application/pdf", "fileSize": 45120, "author": "Admin", ...},
                            {"name": "config.yaml", "mimeType": "text/yaml", "fileSize": 1024, "author": "Dev", ...}]}
""",
    user_description="""Lists all file attachments on an xWiki page, including filename, MIME type, size,
and download URL. Use to discover attached files before reading or deleting them.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

GET_PAGE_ATTACHMENT_TOOL = ToolMetadata(
    name="xwiki_get_page_attachment",
    label="Get Page Attachment",
    description="""Retrieve metadata for a specific file attachment on an xWiki page (name, MIME type,
size, download URL). Does not return file content — use xwiki_read_attachment_content to read
the actual file bytes or extracted text.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- filename (str, required): Exact attachment filename.
  Use xwiki_list_page_attachments to discover filenames.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains the attachment metadata object with:
- 'id': Attachment identifier
- 'name': Attachment filename
- 'mimeType': MIME type (e.g., "application/pdf", "image/png")
- 'fileSize': File size in bytes
- 'author': Username of the uploader
- 'date': Upload timestamp
- 'xwikiAbsoluteUrl': Full URL to download the attachment

Examples:
- Get metadata for a specific attachment:
  wiki: "xwiki", space: "Documentation", page: "Architecture", filename: "diagram.pdf"
  Result: HTTP: GET https://wiki.example.com/rest/.../attachments/diagram.pdf -> 200 OK
          {"name": "diagram.pdf", "mimeType": "application/pdf", "fileSize": 45120,
           "author": "Admin", "xwikiAbsoluteUrl": "https://wiki.example.com/..."}
""",
    user_description="""Retrieves metadata (name, MIME type, size, download URL) for a specific file
attachment on an xWiki page. To read the actual file content, use xwiki_read_attachment_content.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

READ_PAGE_ATTACHMENT_CONTENT_TOOL = ToolMetadata(
    name="xwiki_read_attachment_content",
    label="Read Page Attachment Content",
    description="""Retrieve and parse the actual content of a file attached to an xWiki page.
Returns parsed text or structured content depending on the file type, along with attachment metadata.

Use this tool to read the actual content of files attached to wiki pages — not just their metadata.
Suitable for downstream tasks such as search, summarization, table-of-contents extraction, or archival.

Arguments:
- wiki (str): Wiki identifier (default: xwiki)
- space (str): Space name — dot-separated for nested (e.g. Main.Sandbox)
- page (str): Page name
- filename (str): Exact attachment filename to read (e.g. "report.pdf"). Use xwiki_list_page_attachments first to discover filenames.

File-Type Handling:
- Text files (txt, md, json, xml, csv, yaml, yml, toml, ini, cfg, conf, log, html, rst, etc.): decoded text content is returned.
- PDF: extracted text content. Falls back to structural metadata (page count, dimensions, document properties) when no selectable text is found.
- Images (png, jpg, gif, bmp, webp, etc.): base64-encoded content with a note (AI vision description requires a chat model).
- DOCX (Word): extracted text content from the document.
- PPTX (PowerPoint): extracted text from all slides.
- XLSX / XLSB (Excel): extracted tabular content as text.
- Other/unknown types: base64-encoded content with metadata note.

Return Format:
Returns dict with:
- 'filename': Name of the attachment
- 'mime_type': Detected MIME type
- 'size_bytes': Size of the file in bytes
- 'content_type': How content is represented ('text', 'base64', 'image_description', or 'metadata_only')
- 'content': The parsed content (text, base64 string, image description, or null for metadata_only)
- 'note': Optional message explaining limitations or processing applied

Note: For large binary files (>50 KB) that cannot be parsed to text, the tool returns
content_type='metadata_only' with content=null instead of a base64 blob that would be truncated.

Examples:
- Read a PDF attachment:
  wiki: "xwiki", space: "Main", page: "Architecture", filename: "design.pdf"
  Result: {"filename": "design.pdf", "mime_type": "application/pdf", "content_type": "text",
           "content": "## Page 1\\n\\nThis is the extracted PDF text...", "size_bytes": 45120, "note": null}

- Read a YAML config file:
  wiki: "xwiki", space: "DevOps", page: "Configuration", filename: "config.yaml"
  Result: {"filename": "config.yaml", "mime_type": "text/yaml", "content_type": "text",
           "content": "service:\\n  port: 8080\\n  ...", "size_bytes": 1024, "note": null}

- Read a Word document:
  wiki: "xwiki", space: "Docs", page: "Specs", filename: "requirements.docx"
  Result: {"filename": "requirements.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           "content_type": "text", "content": "# Requirements\\n\\n1. ...", "size_bytes": 32768, "note": null}
""",
    user_description="Read and extract the actual content of a file attachment from an xWiki page. Supports PDF, Word, PowerPoint, Excel, and plain text formats.",
    settings_config=False,
    config_class=XWikiConfig,
)

CREATE_PAGE_ATTACHMENT_TOOL = ToolMetadata(
    name="xwiki_create_page_attachment",
    label="Create Page Attachment",
    description="""Upload one or more files as attachments to an xWiki page. Files must be provided via
the tool's input_files field. All provided files are uploaded in a single call.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier) to attach the file(s) to.

File Input:
- Files must be provided via the tool's input_files configuration field.
- All file types are supported (PDF, images, documents, text files, etc.).
- If no files are provided, the tool raises an error.

Return Format:
Returns one result line per uploaded file:
  'filename': HTTP <status_code> <reason>

On failure for an individual file, raises a ToolException with the error message.

Examples:
- Upload a PDF to a documentation page:
  wiki: "xwiki", space: "Documentation", page: "Architecture"
  [provide report.pdf via input_files]
  Result: 'report.pdf': HTTP 201 Created

- Upload multiple files:
  wiki: "xwiki", space: "Main", page: "Resources"
  [provide config.yaml and diagram.png via input_files]
  Result:
    'config.yaml': HTTP 201 Created
    'diagram.png': HTTP 201 Created
""",
    user_description="""Uploads one or more files as attachments to an xWiki page. All file types are
supported. Files must be provided via the input_files configuration field.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password
4. Files to upload via the input_files field""",
    settings_config=False,
    config_class=XWikiConfig,
)

DELETE_PAGE_ATTACHMENT_TOOL = ToolMetadata(
    name="xwiki_delete_page_attachment",
    label="Delete Page Attachment",
    description="""Permanently delete a file attachment from an xWiki page.

IMPORTANT: This action is irreversible. Use xwiki_list_page_attachments to confirm the exact
filename before deleting.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space name — dot-separated for nested spaces (e.g., "Main.Sandbox").
- page (str, required): Page name (URL identifier, e.g., "MyPage").
- filename (str, required): Exact filename of the attachment to delete.
  Use xwiki_list_page_attachments to confirm filenames.

Return Format:
Returns an HTTP response string:
  HTTP: DELETE <url> -> <status_code> <reason>
  <response body (empty on success)>

On success, the response is HTTP 204 No Content with an empty body.
On failure (e.g., attachment not found), returns HTTP 404 with an error message.

Examples:
- Delete a specific attachment:
  wiki: "xwiki", space: "Documentation", page: "Architecture", filename: "old-diagram.pdf"
  Result: HTTP: DELETE https://wiki.example.com/rest/.../attachments/old-diagram.pdf -> 204 No Content
""",
    user_description="""Permanently deletes a file attachment from an xWiki page. This action is
irreversible — always confirm the filename with xwiki_list_page_attachments before proceeding.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------

SEARCH_WIKI_TOOL = ToolMetadata(
    name="xwiki_search_wiki",
    label="Search Wiki",
    description="""Full-text search across an entire xWiki wiki. Optionally restrict to a specific space
or change the search scope. Use xwiki_search_space for a more focused search when the target
space is already known.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- query (str, required): Search terms or phrase (e.g., "authentication token", "deployment guide").
- scope (str, optional): What to search. Default: "content".
  Options:
  - "content"  → search within page body text (default)
  - "name"     → search page URL names
  - "title"    → search page display titles
  - "spaces"   → search space names
  - "wikis"    → search wiki names (multi-wiki instances)
- space (str, optional): Restrict search to a specific space — dot-separated for nested.
  If omitted, searches the entire wiki.
- number (int, optional): Maximum number of results to return. Default: 10.
- start (int, optional): Pagination offset. Default: 0.
- is_markdown (bool, optional): Convert HTML result snippets to Markdown. Default: false.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "searchResults" array. Each result object includes:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'space': Space name
- 'name': Page name
- 'title': Page title
- 'score': Relevance score (float)
- 'xwikiAbsoluteUrl': Full URL to the matching page

Examples:
- Full-text search for "kubernetes":
  wiki: "xwiki", query: "kubernetes", scope: "content", number: 10
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/search?q=kubernetes&scope=content -> 200 OK
          {"searchResults": [{"id": "xwiki:DevOps.K8sDeploy", "title": "Kubernetes Deployment", "score": 1.8, ...}]}

- Search page titles in a specific space:
  wiki: "xwiki", query: "onboarding", scope: "title", space: "HR"
  Result: Pages in HR space whose titles contain "onboarding"

- Paginate results:
  number: 10, start: 10  → second page of results
""",
    user_description="""Full-text search across an entire xWiki wiki. Supports multiple scopes (content,
title, name) and optional space restriction. Use xwiki_search_space for more focused results
when the target space is already known.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)

SEARCH_SPACE_TOOL = ToolMetadata(
    name="xwiki_search_space",
    label="Search Space",
    description="""Full-text search restricted to a specific xWiki space (including nested sub-spaces).
Use when you already know the relevant space and want more focused results than a wiki-wide search.
Prefer this over xwiki_search_wiki when the target space is known.

Arguments:
- wiki (str, optional): Wiki identifier. Default: "xwiki".
- space (str, required): Space to search within — dot-separated for nested spaces (e.g., "Main.Sandbox").
- query (str, required): Search terms or phrase (e.g., "authentication token").
- number (int, optional): Maximum number of results to return. Default: 10.
- start (int, optional): Pagination offset. Default: 0.
- is_markdown (bool, optional): Convert HTML result snippets to Markdown. Default: false.

Return Format:
Returns an HTTP response string:
  HTTP: GET <url> -> <status_code> <reason>
  <JSON body>

The JSON body contains a "searchResults" array. Each result object includes:
- 'id': Full page identifier (wiki:Space.PageName format)
- 'space': Space name
- 'name': Page name
- 'title': Page title
- 'score': Relevance score (float)
- 'xwikiAbsoluteUrl': Full URL to the matching page

Examples:
- Search within the "Documentation" space:
  wiki: "xwiki", space: "Documentation", query: "REST API authentication", number: 10
  Result: HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Documentation/search?q=REST... -> 200 OK
          {"searchResults": [{"id": "xwiki:Documentation.AuthGuide", "title": "Authentication Guide", "score": 2.1, ...}]}

- Search within a nested space:
  wiki: "xwiki", space: "Main.Sandbox", query: "test page"
""",
    user_description="""Full-text search restricted to a specific xWiki space, including nested sub-spaces.
Use this for more focused results when you already know the relevant space.

Before using it, you need to provide:
1. xWiki base URL (e.g., https://wiki.example.com)
2. Username (for Basic Auth)
3. API token or password""",
    settings_config=False,
    config_class=XWikiConfig,
)
