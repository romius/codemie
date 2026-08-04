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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig

SEARCH_WORK_ITEMS_TOOL = ToolMetadata(
    name="search_work_items",
    description="""
        Search for work items using a WIQL query and dynamically fetch fields based on the query.

        Arguments:
        - query (str): WIQL query for searching Azure DevOps work items
        - limit (int, optional): Number of items to return. If -1, all items are returned. If not provided, uses default limit.
        - fields (list[str], optional): List of requested fields
        """,
    label="Search Work Items",
    user_description="""
        Searches for work items in Azure DevOps using a WIQL (Work Item Query Language) query.
        Returns work items matching the query criteria with specified fields.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

CREATE_WORK_ITEM_TOOL = ToolMetadata(
    name="create_work_item",
    description="""
        Create a work item in Azure DevOps with optional file attachments.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and automatically linked to the work item.
        Supported file types: All file types (PDF, images, documents, logs, etc.)

        Arguments:
        - work_item_json (str): JSON of the work item fields to create in Azure DevOps, i.e.
                                {
                                   "fields":{
                                      "System.Title":"Implement Registration Form Validation",
                                      "System.Description":"Add validation to the registration form",
                                      "System.AssignedTo":"user@example.com"
                                   }
                                }
        - wi_type (str, optional): Work item type, e.g. 'Task', 'Bug', 'Issue' or 'Epic'. Default is "Task"

        File Attachments:
        - Provide files via the config's input_files field
        - All attached files will be uploaded to Azure DevOps
        - Files are automatically linked to the work item as AttachedFile relations
        - Attachments are uploaded using the Azure DevOps Work Item Attachments API

        Example:
        - Create work item with attachments:
          work_item_json: '{"fields":{"System.Title":"Bug Report","System.State":"New"}}'
          wi_type: "Bug"
          [Provide files via input_files: screenshot.png, logs.txt]
          Result: Work item created with 2 file attachments
        """,
    label="Create Work Item",
    user_description="""
        Creates a new work item in Azure DevOps with the specified fields, work item type, and optional file attachments.
        The tool returns a confirmation message with the ID and URL of the created work item, and lists any attached files.

        Supports attaching files (screenshots, logs, documents, etc.) which will be uploaded and linked to the work item.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

UPDATE_WORK_ITEM_TOOL = ToolMetadata(
    name="update_work_item",
    description="""
        Update an existing work item in Azure DevOps with optional file attachments.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and automatically linked to the work item.
        Supported file types: All file types (PDF, images, documents, logs, etc.)

        Note: Work item type cannot be changed after creation. Use create_work_item to create a new work item of a different type.

        Arguments:
        - id (int): ID of the work item to update
        - work_item_json (str): JSON of the work item fields to update in Azure DevOps, i.e.
                                {
                                   "fields":{
                                      "System.Title":"Updated Title",
                                      "System.State":"Active",
                                      "System.AssignedTo":"user@example.com"
                                   }
                                }

        File Attachments:
        - Provide files via the config's input_files field
        - All attached files will be uploaded to Azure DevOps
        - Files are automatically linked to the work item as AttachedFile relations
        - Attachments are uploaded using the Azure DevOps Work Item Attachments API

        Example:
        - Update work item with attachments:
          id: 12345
          work_item_json: '{"fields":{"System.State":"Resolved","System.AssignedTo":"user@example.com"}}'
          [Provide files via input_files: screenshot.png, logs.txt]
          Result: Work item updated with 2 file attachments
        """,
    label="Update Work Item",
    user_description="""
        Updates an existing work item in Azure DevOps with the specified fields and optional file attachments.
        The tool returns a confirmation message with the ID of the updated work item, and lists any attached files.

        Supports attaching files (screenshots, logs, documents, etc.) which will be uploaded and linked to the work item.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_WORK_ITEM_TOOL = ToolMetadata(
    name="get_work_item",
    description="""
        Get a single work item by ID with optional attachment download.

        Arguments:
        - id (int): The work item ID
        - fields (list[str], optional): List of requested fields
        - as_of (str, optional): AsOf UTC date time string
        - expand (str, optional): The expand parameters for work item attributes.
                                  Possible options are { None, Relations, Fields, Links, All }.
        - include_attachments (bool, optional): Whether to download and return attachment content. Default: False.

        Return Format:
        - If include_attachments=False: Returns dict with work item data (backward compatible)
        - If include_attachments=True: Returns dict with work item data plus:
          - 'attachments': dict mapping filename to bytes content
          - 'attachment_count': int (number of attachments downloaded)

        Attachment Download:
        - When include_attachments=True, the tool parses work item relations for AttachedFile relations
        - Downloads all attached files from Azure DevOps
        - Returns file content as bytes for further processing
        - Failed downloads are logged but don't stop the operation

        Examples:
        - Get work item without attachments (default):
          id: 12345
          Result: {\"id\": 12345, \"url\": \"...\", \"fields\": {...}, \"relations\": [...]}
        - Get work item with attachments:
          id: 12345
          include_attachments: True
          Result: {\"id\": 12345, \"url\": \"...\", \"fields\": {...}, \"relations\": [...],
                  \"attachments\": {\"screenshot.png\": b\"...\", \"log.txt\": b\"...\"}, \"attachment_count\": 2}
        """,
    label="Get Work Item",
    user_description="""
        Retrieves a single work item from Azure DevOps by its ID with optional attachment download.
        Returns the work item details including requested fields and relations if specified.

        When include_attachments=True, also downloads and returns the content of all attached files.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

LINK_WORK_ITEMS_TOOL = ToolMetadata(
    name="link_work_items",
    description="""
        Add the relation to the source work item with an appropriate attributes if any.

        Arguments:
        - source_id (int): ID of the work item you plan to add link to
        - target_id (int): ID of the work item linked to source one
        - link_type (str): Link type: System.LinkTypes.Dependency-forward, etc.
        - attributes (dict, optional): Dict with attributes used for work items linking.
                                       Example: 'comment': 'Some linking comment'
        """,
    label="Link Work Items",
    user_description="""
        Creates a link between two work items in Azure DevOps.
        The tool establishes a relationship between a source and target work item with the specified link type.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_RELATION_TYPES_TOOL = ToolMetadata(
    name="get_relation_types",
    description="""
        Returns dict of possible relation types per syntax: 'relation name': 'relation reference name'.
        NOTE: reference name is used for adding links to the work item
        """,
    label="Get Relation Types",
    user_description="""
        Retrieves all available relation types that can be used to link work items in Azure DevOps.
        Returns a dictionary mapping relation names to their reference names.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_COMMENTS_TOOL = ToolMetadata(
    name="get_comments",
    description="""
        Get comments for work item by ID.

        Arguments:
        - work_item_id (int): The work item ID
        - limit_total (int, optional): Max number of total comments to return
        - include_deleted (bool, optional): Specify if the deleted comments should be retrieved
        - expand (str, optional): The expand parameters for comments.
                                  Possible options are { all, none, reactions, renderedText, renderedTextOnly }.
        - order (str, optional): Order in which the comments should be returned. Possible options are { asc, desc }
        """,
    label="Get Comments",
    user_description="""
        Retrieves comments for a specific work item in Azure DevOps.
        Returns a list of comments with their details based on the specified parameters.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

CREATE_COMMENT_TOOL = ToolMetadata(
    name="create_comment",
    description="""
        Add a new comment to a work item in Azure DevOps.

        Arguments:
        - work_item_id (int): The work item ID to add a comment to
        - text (str): The text content of the comment
        """,
    label="Create Comment",
    user_description="""
        Adds a new comment to a specific work item in Azure DevOps.
        Returns a confirmation message with the work item ID and the new comment ID.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_WORK_ITEM_ATTACHMENT_CONTENT_TOOL = ToolMetadata(
    name="get_work_item_attachment_content",
    description="""
        Retrieve and parse the content of a single named attachment on an Azure DevOps work item.
        Returns parsed text or structured content depending on the file type, along with
        attachment metadata and the text note (comment) recorded for the attachment.

        Use this tool for parsed content of a single named attachment. To bulk-download raw bytes
        for all attachments use get_work_item with include_attachments=true.

        Use this tool to read the actual content of files attached to work items — not just
        their metadata. Suitable for tasks such as image description, PDF content elicitation,
        document analysis, or any downstream workflow processing.

        Identification (provide ONE of the following):
        1. Direct URL (RECOMMENDED when available):
           - work_item_id + attachment_url: URL from work item relations
             (contains '/_apis/wit/attachments/')
        2. Discovery via work item ID + attachment name:
           - work_item_id + attachment_name: tool fetches work item relations and
             finds the attachment with matching filename (case-insensitive)

        Arguments:
        - work_item_id (int): The work item ID whose attachment to retrieve.
        - attachment_url (str, optional): Direct URL to the attachment. Takes priority when provided.
        - attachment_name (str, optional): Filename of the attachment (e.g. "report.pdf").
          Case-insensitive. Required when attachment_url is not provided.

        File-Type Handling:
        - Text files (txt, md, json, xml, csv, yaml, etc.): decoded text content is returned.
        - PDF: extracted text (and optionally image text via OCR when LLM is available).
        - Images (png, jpg, gif, bmp, webp, etc.): image description/text via LLM vision when
          available; otherwise base64-encoded content with a note.
        - DOCX (Word): extracted text content from the document.
        - PPTX (PowerPoint): extracted text from all slides.
        - XLSX / XLS / XLSB (Excel): sheet data converted to text.
        - Other/unknown types: base64-encoded content (if ≤ 50 KB) or metadata-only note.

        Return Format:
        Returns dict with:
        - 'work_item_id': ID of the work item
        - 'filename': Name of the attachment
        - 'attachment_note': Text note/comment recorded for the attachment (may be null)
        - 'mime_type': Detected MIME type
        - 'size_bytes': Size of the file in bytes
        - 'content_type': How content is represented ('text', 'base64', 'image_description',
          or 'metadata_only')
        - 'content': The parsed content (text, base64 string, image description, or null)
        - 'note': Optional processing note explaining limitations or fallbacks applied

        Note: For large binary files (>50 KB) that cannot be parsed to text, the tool returns
        content_type='metadata_only' with content=null instead of a base64 blob that would be
        truncated.

        Examples:
        - Retrieve PDF attachment by name:
          work_item_id: 12345
          attachment_name: "requirements.pdf"
          Result: {"work_item_id": 12345, "filename": "requirements.pdf",
                   "attachment_note": "Initial requirements draft",
                   "mime_type": "application/pdf", "content_type": "text",
                   "content": "## Section 1\\n\\nThe system shall...", "size_bytes": 45120, "note": null}

        - Retrieve image attachment via direct URL:
          work_item_id: 42
          attachment_url: "https://dev.azure.com/Org/Proj/_apis/wit/attachments/abc-123?fileName=arch.png"
          Result: {"work_item_id": 42, "filename": "arch.png",
                   "attachment_note": null,
                   "mime_type": "image/png", "content_type": "image_description",
                   "content": "The image shows a microservices architecture diagram...",
                   "size_bytes": 98304, "note": null}

        - Retrieve text file by name:
          work_item_id: 7
          attachment_name: "config.yaml"
          Result: {"work_item_id": 7, "filename": "config.yaml",
                   "attachment_note": "Production config snapshot",
                   "mime_type": "text/yaml", "content_type": "text",
                   "content": "service:\\n  port: 8080\\n  ...", "size_bytes": 1024, "note": null}
        """,
    label="Get Work Item Attachment Content",
    user_description="""
        Retrieves and parses the actual content of a file attached to a work item — not just its
        metadata. Also returns the text note/comment recorded for the attachment, which describes
        what the file contains or why it was attached.

        Supports text extraction from PDFs, Word documents, PowerPoint presentations, Excel sheets,
        images (via AI vision), and plain text files (txt, json, xml, csv, md, yaml, etc.).

        The attachment can be identified by a direct URL (from work item relations) or by specifying
        the work item ID and attachment filename.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with Work Items read permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)
