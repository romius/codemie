# approve_for_me: Tool Safety Classification

## Goal

Make the `APPROVE_FOR_ME` `ToolCallPolicy` work end-to-end. When the policy is
`approve_for_me`, the agent auto-approves safe (read-only) tool calls and only
interrupts for mutating ones. The user never sees a confirmation prompt for
`is_safe() == True` tool calls.

## Background

`ToolCallPolicy` has three values (from `src/codemie/core/enums.py`):

| Value | Meaning |
|---|---|
| `AUTO_APPROVE` | Never interrupt — all tools execute without confirmation |
| `ASK_FOR_APPROVAL` | Always interrupt — every tool call needs user approval |
| `APPROVE_FOR_ME` | **Smart interrupt** — interrupt only for mutating tools, auto-approve safe (read-only) ones |

`APPROVE_FOR_ME` is currently wired into `ToolPermissionsService` and the
effective-permissions chain, but the runtime agent doesn't distinguish it from
`ASK_FOR_APPROVAL` — both set `require_tool_confirmation=True` and always
interrupt. This spec closes that gap.

## Architecture

### Part 1: `is_safe(args: dict) -> bool` on `CodeMieTool`

Add a method to `src/codemie_tools/base/codemie_tool.py`:

```python
def is_safe(self, args: dict) -> bool:
    """Return True if this tool call produces no side effects (safe to auto-approve).

    Default: False (conservative — unknown tools are treated as mutating).
    Override in subclasses with well-known semantics.
    For generic REST tools, inspect args to detect the HTTP method.
    """
    return False
```

**Three override patterns:**

**a) Always-safe tools** — typed tools whose semantics are fixed and read-only:
```python
def is_safe(self, args: dict) -> bool:
    return True
```

**b) Generic REST tools** — mutation depends on the HTTP method in the call args.
All of these use the same pattern; extract it into a module-level helper in
`codemie_tool.py`:

```python
_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

def _http_method_is_safe(args: dict, method_key: str = "method") -> bool:
    """Return True when the HTTP method in args is a safe (read-only) method."""
    method = (args.get(method_key) or "").strip().upper()
    return method in _SAFE_HTTP_METHODS
```

Then in each generic REST tool:
```python
def is_safe(self, args: dict) -> bool:
    return _http_method_is_safe(args)
```

For tools that embed the method inside a nested `query` dict (GitHub, GitLab,
Azure DevOps Git, AWS):
```python
def is_safe(self, args: dict) -> bool:
    query = args.get("query") or {}
    if isinstance(query, str):
        import json
        try:
            query = json.loads(query)
        except Exception:
            return False
    return _http_method_is_safe(query)
```

**c) MCPTool** — external, unknown semantics: leave at default `False`.

### Part 2: Pass `tool_call_policy` to the agent

`LangGraphAgent.__init__` currently receives `require_tool_confirmation: bool`.
Add a second parameter:

```python
tool_call_policy: Optional[ToolCallPolicy] = None,
```

Store as `self.tool_call_policy`. When `require_tool_confirmation=True` and
`tool_call_policy is None`, default to `ASK_FOR_APPROVAL` semantics.

**In `assistant_engine_builder.py` → `configure_agent_kwargs`:**

```python
agent_kwargs["require_tool_confirmation"] = (
    allow_tool_confirmation and permissions.tool_call_policy != ToolCallPolicy.AUTO_APPROVE
)
agent_kwargs["tool_call_policy"] = permissions.tool_call_policy
```

**In `assistant_factory.py` (or wherever `LangGraphAgent` is constructed):**
forward `tool_call_policy` from `agent_kwargs` to the agent constructor.

### Part 3: Auto-resume in `ToolCallConfirmationMixin`

`ask_for_tool_confirmation` is called at the end of `_stream_graph` when the
graph has interrupted before the tools node. Change it to return a `bool`:
`True` means "auto-resume right now", `False` means "normal interrupt, wait for
user".

```python
def ask_for_tool_confirmation(self, state: Any, last_message: str) -> tuple[str, bool]:
    """Returns (last_message, needs_auto_resume)."""
    if not (state.next and "tools" in state.next):
        return last_message, False

    last_ai_message = state.values["messages"][-1]
    if not last_ai_message.tool_calls:
        logger.warning(...)
        return last_message, False

    tool_call = last_ai_message.tool_calls[0]

    # approve_for_me: check if this specific call is safe
    if self.tool_call_policy == ToolCallPolicy.APPROVE_FOR_ME:
        tool = next((t for t in self.tools if t.name == tool_call["name"]), None)
        if tool is not None and tool.is_safe(tool_call.get("args", {})):
            return last_message, True  # auto-resume, no SSE, no saved checkpoint

    # Normal interrupt path (ASK_FOR_APPROVAL or unsafe tool under APPROVE_FOR_ME)
    pending_event = ToolCallPendingEvent(...)
    ConversationCheckpointService().save_pending_tool_call(self.conversation_id, pending_event)
    self.interrupted = True
    # ... send SSE ...
    return last_message, False
```

**In `_stream_graph`** (in `LangGraphAgent`):

```python
if self.require_tool_confirmation:
    state = self.agent_executor.get_state(state_config)
    last_message, needs_auto_resume = self.ask_for_tool_confirmation(state, last_message)
    if needs_auto_resume:
        # Recurse: resume and continue streaming until completion or next interrupt
        return self._stream_graph(None, config, chunks_collector)
```

The recursion is safe and bounded: each recursive call either:
- Produces another safe tool call → auto-resumes again
- Hits an unsafe tool → interrupts normally, sets `self.interrupted = True`, stops
- Completes the agent turn → returns the final answer

### Part 4: `tool_call_policy` availability on the agent

The mixin needs `self.tool_call_policy` and `self.tools`. Both are already on
`LangGraphAgent` (tools at `self.tools`, policy now added). The mixin accesses
them via `self` because `ToolCallConfirmationMixin` is mixed into `LangGraphAgent`.

---

## Tool Classification

### Always-safe (`is_safe` → `True`)

These tools are pure reads. Override `is_safe` to return `True`.

| Tool class | File |
|---|---|
| `SearchKBTool` | `agents/tools/kb/search_kb.py` |
| `RequestUserInputTool` | `agents/tools/interactive/request_user_input.py` |
| `GetAssistantsTool` | `agents/tools/platform/platform_tool.py` |
| `GetConversationMetricsTool` | `agents/tools/platform/platform_tool.py` |
| `GetRawConversationsTool` | `agents/tools/platform/platform_tool.py` |
| `GetSpendingTool` | `agents/tools/platform/platform_tool.py` |
| `GetKeySpendingTool` | `agents/tools/platform/platform_tool.py` |
| `GetConversationAnalyticsTool` | `agents/tools/platform/platform_tool.py` |
| `WebScrapperTool` | `codemie_tools/research/tools.py` |
| `GoogleSearchResults` | `codemie_tools/research/tools.py` |
| `GooglePlacesTool` | `codemie_tools/research/tools.py` |
| `GooglePlacesFindNearTool` | `codemie_tools/research/tools.py` |
| `WikipediaQueryRun` | `codemie_tools/research/tools.py` |
| `ReadFileTool` | `codemie_tools/data_management/file_system/tools.py` |
| `ListDirectoryTool` | `codemie_tools/data_management/file_system/tools.py` |
| `SearchElasticIndex` | `codemie_tools/data_management/elastic/tools.py` |
| `ListWorkspaceFilesTool` | `codemie_tools/data_management/workspace/tools.py` |
| `ReadWorkspaceFileTool` | `codemie_tools/data_management/workspace/tools.py` |
| `GrepWorkspaceFilesTool` | `codemie_tools/data_management/workspace/tools.py` |
| `GetOpenApiSpec` | `codemie_tools/open_api/tools.py` |
| `XrayGetTestsTool` | `codemie_tools/qa/xray/tools.py` |
| `GetExtendedLaunchDataTool` | `codemie_tools/report_portal/tools.py` |
| `GetExtendedLaunchDataAsRawTool` | `codemie_tools/report_portal/tools.py` |
| `SearchWorkItemsTool` | `codemie_tools/azure_devops/work_item/tools.py` |
| `GetWorkItemTool` | `codemie_tools/azure_devops/work_item/tools.py` |
| `GetRelationTypesTool` | `codemie_tools/azure_devops/work_item/tools.py` |
| `GetCommentsTool` | `codemie_tools/azure_devops/work_item/tools.py` |
| `GetWikiTool` (ADO) | `codemie_tools/azure_devops/wiki/tools.py` |
| `ListWikisTool` (ADO) | `codemie_tools/azure_devops/wiki/tools.py` |
| `ListPagesTool` (ADO) | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetWikiPageByPathTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetWikiPageByIdTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetWikiPageCommentsByIdTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetWikiPageCommentsByPathTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetWikiAttachmentContentTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetPageStatsByIdTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `GetPageStatsByPathTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `SearchWikiPagesTool` | `codemie_tools/azure_devops/wiki/tools.py` |
| `ListWikisTool` (XWiki) | `codemie_tools/core/project_management/xwiki/tools.py` |
| `GetWikiTool` (XWiki) | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListSpacesTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `GetSpaceTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListPagesTool` (XWiki) | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListWikiPagesTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListPageChildrenTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `GetPageTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListWikiTagsTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListPageTagsTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListPageCommentsTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `GetPageCommentTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ListPageAttachmentsTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `GetPageAttachmentTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `ReadPageAttachmentContentTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `SearchWikiTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| `SearchSpaceTool` | `codemie_tools/core/project_management/xwiki/tools.py` |
| File-analysis tools (PDF, CSV, XLSX, PPTX, DOCX, Email) | `codemie_tools/file_analysis/*/tools.py` |

**IDE tools** (`agents/tools/ide/`): classified as safe — IDE tools read editor
context; they do not write to the filesystem or external services.

### Always-unsafe (`is_safe` default `False` — no override needed)

These tools always mutate external state. The default `False` is correct; no
override required.

| Tool class | File | Reason |
|---|---|---|
| `WriteFileTool` | `data_management/file_system/tools.py` | Writes local file |
| `WriteWorkspaceFileTool` | `data_management/workspace/tools.py` | Writes workspace file |
| `EditWorkspaceFileTool` | `data_management/workspace/tools.py` | Edits workspace file |
| `DeleteWorkspaceFileTool` | `data_management/workspace/tools.py` | Deletes workspace file |
| `ExecuteWorkspaceScriptTool` | `data_management/workspace/execute_workspace_script_tool.py` | Executes script |
| `CodeExecutorTool` | `data_management/code_executor/code_executor_tool.py` | Executes code |
| `EmailTool` | `notification/email/tools.py` | Sends email |
| `UpdateFileGitTool` | `codemie_tools/git/tools.py` | Commits to git |
| `XrayCreateTestTool` | `qa/xray/tools.py` | Creates test via GraphQL mutation |
| `XrayExecuteGraphQLTool` | `qa/xray/tools.py` | Arbitrary GraphQL — default False |
| `SQLTool` | `data_management/sql/tools.py` | Arbitrary SQL — may mutate |
| `CreateWorkItemTool` | `azure_devops/work_item/tools.py` | Creates work item |
| `UpdateWorkItemTool` | `azure_devops/work_item/tools.py` | Updates work item |
| `LinkWorkItemsTool` | `azure_devops/work_item/tools.py` | Links work items |
| `CreateCommentTool` | `azure_devops/work_item/tools.py` | Creates comment |
| `GetWorkItemAttachmentContentTool` | `azure_devops/work_item/tools.py` | Reads attachment — override `is_safe` → `True` (move to safe list) |
| `DeletePageByPathTool` | `azure_devops/wiki/tools.py` | Deletes page |
| `DeletePageByIdTool` | `azure_devops/wiki/tools.py` | Deletes page |
| `RenameWikiPageTool` | `azure_devops/wiki/tools.py` | Renames page |
| `MoveWikiPageTool` | `azure_devops/wiki/tools.py` | Moves page |
| `CreateWikiPageTool` | `azure_devops/wiki/tools.py` | Creates page |
| `ModifyWikiPageTool` | `azure_devops/wiki/tools.py` | Modifies page |
| `AddWikiAttachmentTool` | `azure_devops/wiki/tools.py` | Adds attachment |
| `AddWikiCommentByIdTool` | `azure_devops/wiki/tools.py` | Adds comment |
| `CreatePageTool` (XWiki) | `xwiki/tools.py` | Creates page |
| `ModifyPageTool` (XWiki) | `xwiki/tools.py` | Modifies page |
| `DeletePageTool` (XWiki) | `xwiki/tools.py` | Deletes page |
| `SetPageTagsTool` (XWiki) | `xwiki/tools.py` | Sets tags |
| `CreatePageCommentTool` (XWiki) | `xwiki/tools.py` | Creates comment |
| `CreatePageAttachmentTool` (XWiki) | `xwiki/tools.py` | Creates attachment |
| `DeletePageAttachmentTool` (XWiki) | `xwiki/tools.py` | Deletes attachment |
| `MCPTool` / `ContextAwareMCPTool` | `service/mcp/toolkit.py` | Unknown external semantics |

### Dynamic (HTTP method in args)

These tools dispatch to different HTTP methods at runtime. Override `is_safe`
to inspect the `method` argument.

| Tool class | File | `method` location in args |
|---|---|---|
| `GenericJiraIssueTool` | `core/project_management/jira/tools.py` | `args["method"]` |
| `GenericConfluenceTool` | `core/project_management/confluence/tools.py` | `args["method"]` |
| `GitlabTool` | `core/vcs/gitlab/tools.py` | `args["query"]["method"]` |
| `GithubTool` | `core/vcs/github/tools.py` | `args["query"]["method"]` |
| `AzureDevOpsGitTool` | `core/vcs/azure_devops_git/tools.py` | `args["query"]["method"]` |
| `GenericAzureTool` | `cloud/azure/tools.py` | `args["method"]` |
| `GenericGCPTool` | `cloud/gcp/tools.py` | `args["method"]` |
| `GenericKubernetesTool` | `cloud/kubernetes/tools.py` | `args["method"]` |
| `GenericAWSTool` | `cloud/aws/tools.py` | `args["query"]["method"]` |
| `ServiceNowTableTool` | `itsm/servicenow/tools.py` | `args["method"]` |
| `KeycloakTool` | `access_management/keycloak/tools.py` | `args["method"]` |
| `SharePointTool` | `data_management/sharepoint/tools.py` | `args["method"]` |
| `ZephyrSquadGenericTool` | `qa/zephyr_squad/tools.py` | `args["method"]` |
| `TelegramTool` | `notification/telegram/tools.py` | `args["method"]` |
| `SonarTool` | `code/sonar/tools.py` | `args["method"]` (GET-only in practice, but has param) |
| `InvokeRestApiBySpec` | `open_api/tools.py` | infer from OpenAPI spec operation — default `False` if method not determinable |

> **Note on `InvokeRestApiBySpec`:** this tool resolves the HTTP method from the
> OpenAPI spec at execute time via `operation_id`. Without parsing the spec
> inside `is_safe`, we can't reliably determine the method. Leave at default
> `False` (always ask) unless future work adds spec-aware classification.

---

## Behavioral contract

| Policy | Tool `is_safe(args)` | Interrupt? | SSE sent? | Checkpoint saved? |
|---|---|---|---|---|
| `AUTO_APPROVE` | any | No | No | No |
| `ASK_FOR_APPROVAL` | any | Yes | Yes | Yes |
| `APPROVE_FOR_ME` | `True` | No (auto-resumed) | No | No |
| `APPROVE_FOR_ME` | `False` | Yes | Yes | Yes |
| `APPROVE_FOR_ME` | tool not found | Yes (conservative) | Yes | Yes |

---

## Testing

Tests needed:

1. **`test_is_safe` per category**
   - One test per generic REST tool: `GET` → `True`, `POST` → `False`, missing method → `False`
   - Spot-check always-safe and always-unsafe tools return expected values

2. **`test_ask_for_tool_confirmation_approve_for_me`** (in `test_tool_call_confirmation_mixin.py` or new file)
   - Safe tool under `APPROVE_FOR_ME` → returns `(last_message, True)`, no SSE, no checkpoint
   - Unsafe tool under `APPROVE_FOR_ME` → returns `(last_message, False)`, SSE sent, checkpoint saved
   - Unknown tool (not in self.tools) under `APPROVE_FOR_ME` → conservative, returns `(last_message, False)`
   - Any tool under `ASK_FOR_APPROVAL` → always `(last_message, False)` regardless of `is_safe`

3. **`test_configure_agent_kwargs`** — assert `agent_kwargs["tool_call_policy"]` is set alongside `require_tool_confirmation`

---

## Files to touch

| File | Change |
|---|---|
| `src/codemie_tools/base/codemie_tool.py` | Add `is_safe()` + `_http_method_is_safe()` helper |
| All tools in classification table (safe section) | Override `is_safe` → `True` |
| All tools in dynamic section | Override `is_safe` to check HTTP method in args |
| `src/codemie/agents/langgraph_agent.py` | Add `tool_call_policy` param, store it; change `_stream_graph` to handle `needs_auto_resume` |
| `src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py` | Change `ask_for_tool_confirmation` signature to return `(str, bool)`; add `approve_for_me` branch |
| `src/codemie/service/assistant/assistant_engine_builder.py` | Set `agent_kwargs["tool_call_policy"]` |
| Wherever `LangGraphAgent` is constructed | Pass `tool_call_policy` kwarg |
| Test files (see Testing section) | New/updated tests |
