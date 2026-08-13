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
from codemie_tools.data_management.sharepoint.models import SharePointConfig

SHAREPOINT_TOOL = ToolMetadata(
    name="sharepoint",
    description="""
    SharePoint Tool for the Microsoft Graph REST API: read, search, create and update SharePoint site
    content (list items, documents, folders).
    You must provide the following args: method, relative_url, and optionally params or raw_content.
    1. 'method': The HTTP method, e.g. 'GET', 'POST', 'PATCH', 'PUT', 'DELETE'.
    2. 'relative_url': Graph API path starting with a single forward slash, relative to
       https://graph.microsoft.com/v1.0. Do not include query parameters in the URL, they must be
       provided separately in 'params'.
    3. 'params': Optional dict — query parameters for GET/DELETE, JSON body for POST/PATCH/PUT.
    4. 'raw_content': Optional raw text body for uploading a text document you generate yourself.
    5. 'file_name': Optional name of a file the user attached to the conversation, to upload as-is.

    Finding sites:
    - Search sites the signed-in user can reach: GET /sites with params {"search": "*"}.
      Narrow it with a term, e.g. params {"search": "Marketing"}.
    - Sites the user follows: GET /me/followedSites
    - The tenant root site: GET /sites/root
    - Resolve a site ID from its URL: GET /sites/{hostname}:/sites/{site-name}
      (e.g. /sites/contoso.sharepoint.com:/sites/TeamSite). Always resolve the site ID first when only
      the site URL is known.
    IMPORTANT: Graph site search is indexed and incomplete. An empty result does NOT mean the user has
    no sites or no access - personal and recently created sites are often missing. Never tell the user
    they have no sites. Say the search returned nothing and ask them for the site URL, then resolve it
    with the path above.

    Common operations:
    - List document libraries (drives): GET /sites/{site-id}/drives
    - List lists: GET /sites/{site-id}/lists
    - Read list items: GET /sites/{site-id}/lists/{list-id}/items with params {"expand": "fields"}
    - Create a list item: POST /sites/{site-id}/lists/{list-id}/items with params {"fields": {"Title": "..."}}
    - Update a list item: PATCH /sites/{site-id}/lists/{list-id}/items/{item-id}/fields with params {"Title": "..."}
    - Upload a file the user attached to the conversation:
      PUT /sites/{site-id}/drive/root:/folder/name.ext:/content with 'file_name' set to the attached
      file name. This is the only way to upload binary formats (.docx, .xlsx, .pptx, .pdf, images) —
      never attempt to reproduce their bytes in 'raw_content'.
    - Upload or overwrite a text document you generate yourself:
      PUT /sites/{site-id}/drive/root:/folder/name.ext:/content with the content in 'raw_content'.
      Text formats only (.txt, .md, .csv, .json, .html, .xml).
    - Read a text document: GET /sites/{site-id}/drive/root:/folder/name.ext:/content
      Do not use this on binary documents; their bytes are not readable as text.
    - Edit an existing text document: GET its content, apply the change to the full text, then PUT the
      whole updated text back to the same path. PUT replaces the file, so always send complete content.

    File paths:
    - '/drive/root:' is ALREADY the root of the site's default document library (shown in SharePoint as
      "Documents", stored as "Shared Documents"). Never prefix a path with "Shared Documents/" or
      "Documents/" - that creates a wrongly nested folder of that name.
      Correct:   /sites/{site-id}/drive/root:/report.md:/content
      Incorrect: /sites/{site-id}/drive/root:/Shared Documents/report.md:/content
    - When the user names a specific file, write to exactly that path. Do not invent folders.
    - For a library other than the default, resolve it first: GET /sites/{site-id}/drives, then use
      /sites/{site-id}/drives/{drive-id}/root:/path:/content

    For read operations request only the fields you need ($select) and limit results ($top), until the
    user explicitly asks for more.
    ALWAYS report the 'webUrl' from the response for every item or file you create, update or locate,
    as a clickable link, so the user can open it. If a response has no 'webUrl', give the site or file
    path you used instead.
    """,
    label="SharePoint",
    user_description="""
    Provides access to the Microsoft Graph API for SharePoint Online, enabling the AI assistant to read,
    create and update content on SharePoint sites: list items, documents and folders.

    Key capabilities:
    - Create and update list items on SharePoint lists
    - Upload files attached to the chat, in any format including Word, Excel, PowerPoint and PDF
    - Create and overwrite text documents the assistant writes itself (.txt, .md, .csv, .json, .html)
    - Browse sites, lists and document libraries
    - Search and read site content

    Setup (one time, requires an Azure administrator):
    1. Register an application in Microsoft Entra ID (Azure AD).
    2. Grant it 'Sites.ReadWrite.All' and 'Files.ReadWrite.All' as APPLICATION permissions and grant
       admin consent. To restrict access to specific sites instead of the whole tenant, use the
       'Sites.Selected' permission and assign sites to the application explicitly.
    3. Create a SharePoint integration in the settings providing:
       - Alias (a friendly name for the integration)
       - URL (SharePoint tenant root URL, e.g. https://contoso.sharepoint.com)
       - Tenant ID, Client ID and Client Secret of the Azure application

    Security note: the assistant acts as the Azure application, not as the chatting user. The
    application's permissions in Azure define which sites can be read and modified — anyone allowed to
    use an assistant with this tool can act within those permissions.
    """.strip(),
    settings_config=True,
    config_class=SharePointConfig,
)
