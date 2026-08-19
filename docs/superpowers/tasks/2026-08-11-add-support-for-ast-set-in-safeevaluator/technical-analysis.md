# Technical Research

**Task**: SafeEvaluator ast workflow conditional
**Generated**: 2026-08-11T00:00:00Z

---

## 1. Original Context

Add support for ast.Set in _SafeEvaluator. The _SafeEvaluator is a class that safely evaluates Python AST expressions. It currently handles various AST node types but lacks support for ast.Set (Python set literals like {1, 2, 3}). We need to add an ast.Set visitor method so set literals can be evaluated.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/workflows/utils/safe_eval.py` — The sole implementation file. Contains `_SafeEvaluator` (private recursive AST walker), `SafeEvalError` (typed exception), `safe_eval` (public entry point), `ALLOWED_FUNCTIONS` dict, `_COMPARE_OPS` dict, and `_BIN_OPS` dict.
  - `_SafeEvaluator.__init__` builds `self._handlers: dict[type, Any]` mapping 16 `ast.*` node types to handler methods.
  - Currently registered handlers: `ast.Expression`, `ast.Constant`, `ast.Name`, `ast.UnaryOp`, `ast.BinOp`, `ast.BoolOp`, `ast.Compare`, `ast.IfExp`, `ast.Attribute`, `ast.Subscript`, `ast.List`, `ast.Tuple`, `ast.Dict`, `ast.Call`, `ast.Slice`, `ast.JoinedStr`, `ast.FormattedValue`.
  - `ast.Set` is **absent** from `_handlers`; any set literal expression causes `SafeEvalError("Disallowed expression type: Set")`.
  - `set` is already present in `ALLOWED_FUNCTIONS` (line 44), so `set(...)` call-style works; only the literal `{1, 2, 3}` syntax fails.

- `src/codemie/workflows/utils/__init__.py` — Re-exports `safe_eval` and `SafeEvalError` as the public API of `codemie.workflows.utils`.

- `src/codemie/workflows/utils/utils.py` — `evaluate_conditional_route` (line 347) and `evaluate_next_candidate` (line 382) call `safe_eval` to resolve workflow branching conditions. This is the primary consumer of the evaluator within the routing layer.

- `src/codemie/workflows/nodes/transform_node.py` — `TransformNode._safe_eval` (line 401) wraps `safe_eval` and calls it at lines 394, 483, and 546 for condition evaluation, script evaluation, and item-filter conditions respectively. A second consumer.

- `src/codemie/service/tools/hedging_tool_service.py` — `HedgingToolService` calls `safe_eval` at line 173 for condition evaluation in tool hedging logic. A third consumer.

- `src/codemie_tools/data_management/code_executor/ast_security_checker.py` — Contains a comment on line 21 noting it is "Pattern based on codemie/workflows/utils/safe_eval.py"; it does not import from `safe_eval` but uses a parallel pattern.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| Workflow Utility | `src/codemie/workflows/utils/safe_eval.py` — `_SafeEvaluator` class | Yes — add `_handle_set` method and register `ast.Set` in `_handlers` |
| Workflow Utility | `src/codemie/workflows/utils/__init__.py` | No — public exports unchanged |
| Workflow Routing | `src/codemie/workflows/utils/utils.py` | No — consumer unchanged |
| Workflow Node | `src/codemie/workflows/nodes/transform_node.py` | No — consumer unchanged |
| Service Tool | `src/codemie/service/tools/hedging_tool_service.py` | No — consumer unchanged |

The change is entirely confined to a single method addition and a one-line handler registration inside `safe_eval.py`. No API, repository, database, or external service layer is touched.

### Integration Points

- All three callsite consumers (`utils.py`, `transform_node.py`, `hedging_tool_service.py`) call through the public `safe_eval(expr, local_vars)` function — they receive the new capability transparently with no changes required.
- The `ALLOWED_FUNCTIONS` dict already exposes `set` (the callable), so `set({1, 2})` and `set(items)` already work; the new handler enables only the literal brace syntax.

### Patterns and Conventions

The `_handle_list`, `_handle_tuple`, and `_handle_dict` methods establish the exact pattern to follow:

```python
# _handle_list (line 202–203)
def _handle_list(self, node: ast.List) -> Any:
    return [self.eval(el) for el in node.elts]

# _handle_tuple (line 205–206)
def _handle_tuple(self, node: ast.Tuple) -> Any:
    return tuple(self.eval(el) for el in node.elts)
```

`ast.Set` has the same `elts` attribute as `ast.List` and `ast.Tuple`. The new handler follows the same pattern:

```python
def _handle_set(self, node: ast.Set) -> Any:
    return {self.eval(el) for el in node.elts}
```

Registration follows the `_handlers` dict convention in `__init__`:

```python
ast.Set: self._handle_set,
```

No base class, no decorator, no factory. The dispatch mechanism is a plain dict lookup in `eval()` (line 108).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — Covers workflow executor, nodes, and state transitions. States: "Manually evaluating transition expressions in feature code" should be avoided in favour of workflow utility functions. Confirms `safe_eval` is the sanctioned evaluator.
- `.ai-run/guides/testing/testing-patterns.md` — Confirms pytest, `tests/` structure mirroring `src/`, and policy that tests should match the nearest existing test directory.

### Architectural Decisions

- The whitelist dispatch pattern (`_handlers` dict + typed `SafeEvalError`) is an explicit security decision documented in the module docstring: "Replaces eval() with a whitelist-based AST walker that only permits safe node types."
- Adding `ast.Set` to the whitelist is unambiguously safe: it constructs a Python `set` from already-evaluated sub-expressions, which are themselves already subject to the whitelist. No new attack surface is introduced.
- The `ALLOWED_FUNCTIONS` dict already contains `set`, establishing intent that set construction is an approved operation.

### Derived Conventions

- All collection literal handlers (`_handle_list`, `_handle_tuple`, `_handle_dict`) are grouped together (lines 202–209) immediately before `_handle_call`. The new `_handle_set` should be placed in the same block for consistency.
- Handler method names follow `_handle_<ast_class_lowercase>` naming (e.g., `_handle_list` for `ast.List`), so the new method is `_handle_set`.
- Type annotations use `-> Any` on all handler methods.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/workflows/utils/test_safe_eval.py` — Comprehensive unit test file with three classes:
  - `TestSafeEvalAllowedExpressions` — Tests all currently supported node types. Includes `test_list_literal` (line 103), `test_tuple_literal` (line 106), `test_dict_literal` (line 109). No set literal test exists.
  - `TestSafeEvalBlockedExpressions` — Security regression tests. Includes `test_set_discard_mutation_blocked` (line 265) which tests that `set.discard(s, 1)` via built-in method access is blocked — but this tests the attribute/method-call path, not the `ast.Set` literal path.
  - `TestSafeEvalEdgeCases` — Edge cases including empty input, `SyntaxError` propagation, and a complex workflow expression.

### Testing Framework and Patterns

- Framework: `pytest` (import at line 17; configured in `pytest.ini`).
- No fixtures or conftest used in `test_safe_eval.py`; all tests are self-contained methods on plain classes.
- Assertions use `assert` statements and `pytest.raises` context managers with optional `match=` regex.
- No mocking — `safe_eval` is tested against real Python AST parsing.
- Test method naming: `test_<concept>` (e.g., `test_list_literal`, `test_membership_in`).

### Coverage Gaps

- **No test for `ast.Set` literal syntax** (`{1, 2, 3}`). This is the primary gap the task closes.
- Supplementary gaps to consider:
  - Set literal with variable elements: `{a, b}` where `a` and `b` are local vars.
  - Set membership: `1 in {1, 2, 3}` (exercises `ast.In` on a set, not just a list).
  - Empty set: cannot be expressed as `{}` in Python (that is a dict), so `set()` is already tested via `_handle_call`; this edge case likely does not apply to `ast.Set`.
  - Set comprehension (`{x for x in items}`) — this is `ast.SetComp`, not `ast.Set`, and should remain blocked.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables are referenced in `safe_eval.py` or its direct consumers in the routing/transform path. The evaluator is a pure in-process utility.

### Configuration Files

No configuration files govern `_SafeEvaluator` behaviour. The whitelist (`ALLOWED_FUNCTIONS`, `_BIN_OPS`, `_COMPARE_OPS`, `_handlers`) is defined entirely in source code.

### Feature Flags and Deployment Concerns

None. This is a pure Python in-process change with no deployment, migration, or feature-flag surface area.

---

## 6. Risk Indicators

- **No existing test for `ast.Set` literal** — the gap is clear and expected given the feature is absent; new tests are required alongside the implementation.
- **`ast.SetComp` must remain blocked** — set comprehensions (`{x for x in items}`) share superficial similarity to set literals but use `ast.SetComp`, which is not `ast.Set`. Existing test `test_dict_comprehension_blocked` (line 202) and `test_list_comprehension_blocked` (line 198) demonstrate the pattern; a parallel `test_set_comprehension_blocked` should be added to guard against accidental `ast.SetComp` inclusion.
- **`_handle_dict` uses `zip(node.keys, node.values, strict=False)`** which is slightly more complex; `_handle_set` is simpler (only `node.elts`) — no risk, but worth noting the dict handler is not the right model for set.
- **Three consumers of `safe_eval`** — all are passive consumers that call through `safe_eval()`; none require changes. Risk of regression is negligible.
- **`set` already in `ALLOWED_FUNCTIONS`** — confirms organisational intent. No contradiction between the whitelist and the new handler.

---

## 7. Summary for Complexity Assessment

The task touches a single architectural layer — the Workflow Utility layer — and requires changes to exactly one file: `src/codemie/workflows/utils/safe_eval.py`. The implementation is a two-part, two-line change: add a `_handle_set` method (one line body returning a set comprehension over `node.elts`) and register `ast.Set: self._handle_set` in the `_handlers` dict inside `__init__`. No API endpoints, database models, service orchestration, or external integrations are involved.

The task follows an established and well-understood pattern. `ast.List`, `ast.Tuple`, and `ast.Dict` handlers already exist and are grouped consecutively (lines 202–209). The new `ast.Set` handler is structurally identical to `_handle_list` with a set comprehension instead of a list comprehension. There is zero technical novelty; the only decision is placement (adjacent to the other collection literal handlers) and naming (`_handle_set`, consistent with the existing convention).

Test coverage posture is strong for the existing node types but has a targeted gap for `ast.Set`. The existing test file `tests/codemie/workflows/utils/test_safe_eval.py` is well-structured with three named test classes and clear per-feature test methods. New tests should be added to `TestSafeEvalAllowedExpressions` (at minimum `test_set_literal`) and `TestSafeEvalBlockedExpressions` (`test_set_comprehension_blocked` to guard `ast.SetComp`). Overall complexity is minimal: one implementation file, one test file, zero migration or configuration burden.
