# ast.Set Support in _SafeEvaluator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `_handle_set` to `_SafeEvaluator` so set literals like `{1, 2, 3}` evaluate correctly instead of raising `SafeEvalError`.

**Architecture:** Add one handler method (`_handle_set`) to `_SafeEvaluator` following the identical pattern of `_handle_list`/`_handle_tuple`, and register `ast.Set` in the `_handlers` dict. No other files change.

**Tech Stack:** Python `ast` module, pytest.

## Global Constraints

- Only `src/codemie/workflows/utils/safe_eval.py` and `tests/codemie/workflows/utils/test_safe_eval.py` change.
- `ast.SetComp` must remain blocked — it is a distinct node type and must not be added.
- Handler method name: `_handle_set` (matches `_handle_<ast_class_lowercase>` convention).
- Return type annotation: `-> Any` (matches all other handlers).
- Placement: insert `_handle_set` between `_handle_tuple` (line 205) and `_handle_dict` (line 208).

---

### Task 1: Add ast.Set handler and tests

**Files:**
- Modify: `src/codemie/workflows/utils/safe_eval.py:87-105` (`_handlers` dict), `safe_eval.py:205-207` (new method after `_handle_tuple`)
- Test: `tests/codemie/workflows/utils/test_safe_eval.py`

**Interfaces:**
- Consumes: `ast.Set.elts` — list of `ast.AST` nodes, same as `ast.List.elts` and `ast.Tuple.elts`
- Produces: `_handle_set(node: ast.Set) -> Any` — returns a `set` built from evaluated elements; registered as `ast.Set: self._handle_set` in `_handlers`

- [ ] **Step 1: Write the failing tests**

Add to `TestSafeEvalAllowedExpressions` in `tests/codemie/workflows/utils/test_safe_eval.py`, after `test_dict_literal` (line 110):

```python
def test_set_literal(self):
    assert safe_eval("{1, 2, 3}", {}) == {1, 2, 3}
    assert safe_eval("{a, b}", {"a": 1, "b": 2}) == {1, 2}

def test_set_membership(self):
    assert safe_eval("1 in {1, 2, 3}", {}) is True
    assert safe_eval("4 not in {1, 2, 3}", {}) is True
```

Add to `TestSafeEvalBlockedExpressions` in the same file, after `test_dict_comprehension_blocked` (line 201):

```python
def test_set_comprehension_blocked(self):
    with pytest.raises(SafeEvalError, match="Disallowed expression type"):
        safe_eval("{x for x in items}", {"items": [1, 2]})
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yaroslav_konoplov/projects/codemie-stack/codemie
poetry run pytest tests/codemie/workflows/utils/test_safe_eval.py::TestSafeEvalAllowedExpressions::test_set_literal tests/codemie/workflows/utils/test_safe_eval.py::TestSafeEvalAllowedExpressions::test_set_membership tests/codemie/workflows/utils/test_safe_eval.py::TestSafeEvalBlockedExpressions::test_set_comprehension_blocked -v
```

Expected: `test_set_literal` and `test_set_membership` FAIL with `SafeEvalError: Disallowed expression type: Set`. `test_set_comprehension_blocked` PASS (SetComp already blocked).

- [ ] **Step 3: Add `_handle_set` method to `_SafeEvaluator`**

In `src/codemie/workflows/utils/safe_eval.py`, add after `_handle_tuple` (after line 206):

```python
def _handle_set(self, node: ast.Set) -> Any:
    return {self.eval(el) for el in node.elts}
```

- [ ] **Step 4: Register `ast.Set` in `_handlers`**

In `_SafeEvaluator.__init__`, in the `_handlers` dict (around line 99), add after the `ast.Tuple` entry:

```python
ast.Set: self._handle_set,
```

The updated block (lines 98–101 area) becomes:

```python
ast.List: self._handle_list,
ast.Tuple: self._handle_tuple,
ast.Set: self._handle_set,
ast.Dict: self._handle_dict,
```

- [ ] **Step 5: Run all new tests to confirm GREEN**

```bash
poetry run pytest tests/codemie/workflows/utils/test_safe_eval.py::TestSafeEvalAllowedExpressions::test_set_literal tests/codemie/workflows/utils/test_safe_eval.py::TestSafeEvalAllowedExpressions::test_set_membership tests/codemie/workflows/utils/test_safe_eval.py::TestSafeEvalBlockedExpressions::test_set_comprehension_blocked -v
```

Expected: all three PASS.

- [ ] **Step 6: Run full test file to verify no regressions**

```bash
poetry run pytest tests/codemie/workflows/utils/test_safe_eval.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/workflows/utils/safe_eval.py tests/codemie/workflows/utils/test_safe_eval.py
git commit -m "EPMCDME-13762: Add ast.Set support to _SafeEvaluator"
```

- [ ] **Test-first: yes — `test_set_literal` and `test_set_membership` fail with `SafeEvalError: Disallowed expression type: Set` before the handler is added**
