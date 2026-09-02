# Technical Research

**Task**: workflow abort subworkflow execution
**Generated**: 2026-09-01T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

When a parent workflow that has started a sub-workflow execution is aborted, the sub-workflow keeps running instead of also being aborted.

Steps to Reproduce:
1. Run a parent workflow that starts a sub-workflow execution.
2. While the sub-workflow execution is in progress, abort the parent workflow.

Expected Result: Aborting the parent workflow should also abort the in-progress sub-workflow execution.
Actual Result: The parent workflow is aborted successfully, but the sub-workflow execution remains "in progress" and continues running.

This is a frontend issue in the codemie-ui React/TypeScript app. The fix likely involves finding where the workflow abort action is triggered in the UI and ensuring any active sub-workflow executions are also aborted.

---

## 2. Codebase Findings

### Existing Implementations

**Abort entry points — two components call `abortWorkflowExecution` directly:**

- `src/pages/workflows/details/WorkflowExecutionHeader.tsx` (lines 63–69)
  - The top-level "Abort" button shown when the parent execution is not in a final status.
  - Calls `workflowExecutionsStore.abortWorkflowExecution(String(workflow.id), execution.execution_id)` with the parent's `workflow.id` and `execution.execution_id` only.
  - No logic to discover or abort any in-progress sub-workflow execution.

- `src/pages/workflows/details/states/WorkflowExecutionStateControls.tsx` (lines 69–73)
  - The per-state "Abort" button shown in the details drawer when an execution state is `Interrupted`.
  - Calls `workflowExecutionsStore.abortWorkflowExecution(workflowId, executionId)` via `useExecutionsContext()`.
  - Same gap: only aborts the parent execution context; no sub-workflow awareness.

**Store method that issues the abort API call:**

- `src/store/workflowExecutions.ts` — `abortWorkflowExecution(workflowId, executionId)` (lines 763–779)
  - Issues `PUT v1/workflows/${workflowId}/executions/${executionId}/abort`.
  - After the call, re-fetches the execution and its states for the parent.
  - Has no knowledge of sub-workflow executions; no iteration over child execution IDs.

**Sub-workflow node type — defined but execution-level data is absent from types:**

- `src/types/workflowEditor/base.ts` — `NodeTypes.SUB_WORKFLOW = 'sub_workflow'`
- `src/types/workflowEditor/configuration.ts` — `SubWorkflowStateConfiguration { workflow_id: string }`
  - Only carries the target `workflow_id`; no field for the child `execution_id` spawned at runtime.
- `src/types/entity/workflow.ts` — `WorkflowExecutionState` has no field linking a state to a child execution ID (no `sub_execution_id`, `linked_execution_id`, or similar).

**Execution state data available at abort time:**

- `src/store/workflowExecutions.ts` — `executionStates: WorkflowExecutionState[]`
  - Each state carries `execution_id` (the parent execution ID), `name`, `status`, and `state_id` (the node config ID).
  - States whose `state_id` resolves to a `SUB_WORKFLOW` node would be identifiable by cross-referencing with `SubWorkflowStateConfiguration`.
  - However, the type currently provides no runtime child `execution_id` to abort — the backend would need to expose it, or the frontend must derive it from another API call.

**Execution polling and status tracking:**

- `src/pages/workflows/details/hooks/useExecutionStates.ts` — polls parent execution states every 4 seconds while status is non-final.
  - No polling or awareness of child (sub-workflow) executions.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| API / Store | `src/store/workflowExecutions.ts` — `abortWorkflowExecution` must be extended or a new method added to abort a child execution |
| Service / Business Logic | Both abort call sites in `WorkflowExecutionHeader.tsx` and `WorkflowExecutionStateControls.tsx` must be updated to also abort active sub-workflow executions |
| Type / Data Model | `WorkflowExecutionState` may need a new field (e.g. `sub_execution_id`) if the backend exposes the child execution ID on the state record — this needs backend confirmation |

### Integration Points

- **API endpoint** `PUT v1/workflows/{workflowId}/executions/{executionId}/abort` — must be called once per active sub-workflow execution. The child execution's `workflow_id` comes from `SubWorkflowStateConfiguration.workflow_id`; the child `execution_id` must come from the backend (either on the parent execution state or via a separate lookup).
- **`workflowExecutionsStore.executionStates`** — the array of states already in memory at abort time is the source used to discover which states are sub-workflow nodes currently in progress.
- **`workflowsStore.getSelectableWorkflows`** — the workflows store has `getSelectableWorkflows` targeting `v1/workflows/sub-workflow-candidates`; this is unrelated to runtime abort but confirms sub-workflow data is scoped to the workflows store.

### Patterns and Conventions

- All abort, resume, and delete operations on executions go through the Valtio `workflowExecutionsStore` in `src/store/workflowExecutions.ts`. A new method (or extension to `abortWorkflowExecution`) should follow the same pattern: `async method(workflowId, executionId)` calling `api.put(...)`.
- UI components do not call `api` directly; they always delegate to the store.
- Error handling: each store method wraps the call in a `try/catch` and `console.error`s on failure; the same pattern must be applied to any new abort logic.
- Toast messages use `toaster.info(...)` from `src/utils/toaster`; the existing "Workflow execution aborted" message is issued by the calling component, not the store.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Relevant guides available under `.ai-run/guides/`:
- `.ai-run/guides/development/api-integration.md` — covers how to add API calls using the `api` utility, error handling, and constants.
- `.ai-run/guides/patterns/state-management.md` — covers Valtio store patterns and how to add methods.
- `.ai-run/guides/architecture/architecture.md` — describes where changes belong.

### Architectural Decisions

No ADR found specifically for sub-workflow abort propagation. The existing `abortWorkflowExecution` comment (lines 756–759 in `workflowExecutions.ts`) documents only the parent-scoped behavior: "Abort a running workflow execution. Refreshes the execution state after aborting."

### Derived Conventions

- Store methods that call an API endpoint follow the signature `async methodName(workflowId: string, executionId: string): Promise<Response>` and always call a follow-up getter to refresh local state.
- The `WorkflowExecutionState.execution_id` field currently always holds the **parent** execution ID (confirmed by usage in `WorkflowStateCallHistory.tsx` which passes `state.execution_id` to load thoughts for the parent execution). If the backend adds `sub_execution_id` to state records, the type extension must go in `src/types/entity/workflow.ts`.

---

## 4. Testing Landscape

### Existing Coverage

- `src/pages/workflows/details/__tests__/WorkflowExecutionHeader.test.tsx` — covers the header's abort button rendering and click behavior; will need a new case for sub-workflow abort.
- `src/pages/workflows/details/states/__tests__/WorkflowExecutionStateControls.test.tsx` — covers the state-level abort button; will need a new case.
- `src/store/__tests__/` — has tests for `workflowExecutions` store methods (e.g. `removeByConversation`); a new test file or cases for `abortWorkflowExecution` with sub-workflow propagation should be added here.

### Testing Framework and Patterns

- Vitest with React Testing Library (`src/pages/workflows/details/__tests__/`).
- Store tests use direct mutation of the proxy store and mock `api` calls.
- Component tests render with `render()`, mock store snapshots via `vi.mock('@/store/workflowExecutions')`, and assert on DOM and mock call counts.

### Coverage Gaps

- `abortWorkflowExecution` in `workflowExecutionsStore` has no dedicated unit test covering the abort+refresh flow — and no test at all covering sub-workflow abort.
- `WorkflowExecutionHeader.handleAbort` has no test asserting it discovers and aborts child executions.
- `WorkflowExecutionStateControls.abortWorkflow` has no test asserting child execution abort.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables or feature flags directly govern the abort behavior. The sub-workflow node type is gated behind `useSubWorkflowEnabled` (feature flag) in the editor sidebar and `AdvancedConfigTab`, but the abort logic is not behind a flag — the abort button is always shown when the execution is non-final.

### Configuration Files

No configuration files are involved in the abort flow.

### Feature Flags and Deployment Concerns

- `useSubWorkflowEnabled` (`src/hooks/useFeatureFlags.ts` lines 120–121) gates sub-workflow node creation in the editor. At the time of research this hook contained a `TODO: remove before merge — force-enables sub-workflow for local dev` comment, indicating the feature is still behind a flag. If the fix ships before the flag is removed, it should still function correctly when the flag is off (no sub-workflow states will be present, so the added logic is a no-op).

---

## 6. Risk Indicators

- **Backend may not expose child execution ID on parent state records.** `WorkflowExecutionState` has no `sub_execution_id` field. If the backend does not provide this data, the frontend cannot determine which child execution to abort without an additional API call (e.g. `GET v1/workflows/{subWorkflowId}/executions?parent_execution_id=...`). This is the highest-risk unknown and requires backend coordination before the frontend fix can be completed.

- **No test coverage for `abortWorkflowExecution` store method.** The existing abort flow has no unit test. Any extension will be adding to untested code.

- **Two abort call sites must be updated in sync.** `WorkflowExecutionHeader.tsx` and `WorkflowExecutionStateControls.tsx` both independently invoke abort. If only one is updated the bug is partially fixed.

- **Sub-workflow node type gated by feature flag.** `useSubWorkflowEnabled` — if the flag is off, no sub-workflow states appear, so the fix code path is unreachable in gated environments until the flag is enabled. Integration testing will require the flag to be on.

- **`WorkflowExecutionState.execution_id` semantics.** Currently this field holds the parent execution ID on every state record. If the backend changes it to hold the child execution ID for sub-workflow states, it would be a breaking API contract change affecting multiple consumers in the codebase.

- **No existing E2E or integration test for sub-workflow execution lifecycle.** The only integration test for the workflow details page is `WorkflowDetailsPage.integration.test.tsx`; it does not cover sub-workflow scenarios.

---

## 7. Summary for Complexity Assessment

The task touches two architectural layers: the API/Store layer (`workflowExecutions.ts` — `abortWorkflowExecution`) and the UI/component layer (two abort call sites in `WorkflowExecutionHeader.tsx` and `WorkflowExecutionStateControls.tsx`). The likely file change surface is 3–5 files: the store, the two components, and potentially the `WorkflowExecution` or `WorkflowExecutionState` type definitions if a new field is needed. If the backend exposes child execution IDs, the fix in each component is a targeted addition: after the parent abort call, iterate over `executionStates` to find states whose node type is `SUB_WORKFLOW` and status is non-final, then call `abortWorkflowExecution` for each child.

The primary technical risk is a **dependency on backend changes**. The `WorkflowExecutionState` type currently carries no field that links a running sub-workflow state to its child `execution_id`. Without that data, the frontend cannot construct the abort request for the child. This could require a new backend field on the state record, or an additional API call at abort time. This is a coordination dependency that must be resolved before the frontend implementation can be finalized, and it introduces meaningful uncertainty around scope.

Test coverage posture is weak for this area: `abortWorkflowExecution` has no dedicated unit tests, and there are no integration or E2E tests for sub-workflow execution lifecycle. New tests will be needed for the store method extension and for both component abort handlers. The sub-workflow feature is also still behind a feature flag (`useSubWorkflowEnabled`), so manual verification will require the flag enabled, and automated tests will need to mock or set the flag accordingly.
