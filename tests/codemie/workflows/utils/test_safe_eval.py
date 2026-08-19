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

"""Tests for the AST-based safe expression evaluator."""

import pytest

from codemie.workflows.utils.safe_eval import SafeEvalError, safe_eval
from codemie.workflows.utils import DotDict


class TestSafeEvalAllowedExpressions:
    """Verify that legitimate workflow expressions evaluate correctly."""

    def test_equality_comparison(self):
        assert safe_eval("status == 'success'", {"status": "success"}) is True
        assert safe_eval("status == 'failed'", {"status": "success"}) is False

    def test_inequality_comparison(self):
        assert safe_eval("priority != 'low'", {"priority": "high"}) is True

    def test_numeric_comparisons(self):
        assert safe_eval("value > 50", {"value": 100}) is True
        assert safe_eval("count <= 10", {"count": 10}) is True
        assert safe_eval("count < 10", {"count": 10}) is False

    def test_chained_comparison(self):
        assert safe_eval("1 < value < 100", {"value": 50}) is True
        assert safe_eval("1 < value < 100", {"value": 200}) is False

    def test_boolean_and(self):
        assert safe_eval("a and b", {"a": True, "b": True}) is True
        assert safe_eval("a and b", {"a": True, "b": False}) is False

    def test_boolean_or(self):
        assert safe_eval("a or b", {"a": False, "b": True}) is True
        assert safe_eval("a or b", {"a": False, "b": False}) is False

    def test_boolean_not(self):
        assert safe_eval("not active", {"active": False}) is True

    def test_combined_boolean(self):
        vars = {"status": "success", "count": 10}
        assert safe_eval("status == 'success' and count > 5", vars) is True
        assert safe_eval("status == 'failed' or count > 5", vars) is True

    def test_arithmetic_add(self):
        assert safe_eval("a + b", {"a": 3, "b": 4}) == 7

    def test_arithmetic_subtract(self):
        assert safe_eval("a - b", {"a": 10, "b": 3}) == 7

    def test_arithmetic_multiply(self):
        assert safe_eval("price * quantity", {"price": 10, "quantity": 5}) == 50

    def test_arithmetic_divide(self):
        assert safe_eval("total / 4", {"total": 100}) == 25.0

    def test_arithmetic_floor_divide(self):
        assert safe_eval("total // 3", {"total": 10}) == 3

    def test_arithmetic_modulo(self):
        assert safe_eval("n % 3", {"n": 10}) == 1

    def test_membership_in(self):
        assert safe_eval("status in ['open', 'pending']", {"status": "open"}) is True
        assert safe_eval("status in ['open', 'pending']", {"status": "closed"}) is False

    def test_membership_not_in(self):
        assert safe_eval("status not in ['closed', 'cancelled']", {"status": "open"}) is True

    def test_subscript_access(self):
        assert safe_eval('item["key"]', {"item": {"key": "value"}}) == "value"

    def test_subscript_index(self):
        assert safe_eval("items[0]", {"items": [10, 20, 30]}) == 10
        assert safe_eval("items[-1]", {"items": [10, 20, 30]}) == 30

    def test_dot_notation_with_dotdict(self):
        data = DotDict({"id": 42, "title": "test"})
        assert safe_eval("pr.id", {"pr": data}) == 42
        assert safe_eval("pr.title", {"pr": data}) == "test"

    def test_nested_dot_notation(self):
        data = DotDict({"user": DotDict({"name": "Alice"})})
        assert safe_eval("data.user.name", {"data": data}) == "Alice"

    def test_ternary_expression(self):
        assert safe_eval("'yes' if active else 'no'", {"active": True}) == "yes"
        assert safe_eval("'yes' if active else 'no'", {"active": False}) == "no"

    def test_list_literal(self):
        assert safe_eval("[1, 2, 3]", {}) == [1, 2, 3]

    def test_tuple_literal(self):
        assert safe_eval("(1, 2, 3)", {}) == (1, 2, 3)

    def test_dict_literal(self):
        assert safe_eval("{'a': 1, 'b': 2}", {}) == {"a": 1, "b": 2}

    def test_set_literal(self):
        assert safe_eval("{1, 2, 3}", {}) == {1, 2, 3}
        assert safe_eval("{a, b}", {"a": 1, "b": 2}) == {1, 2}

    def test_set_membership(self):
        assert safe_eval("1 in {1, 2, 3}", {}) is True
        assert safe_eval("4 not in {1, 2, 3}", {}) is True

    def test_negative_number(self):
        assert safe_eval("-1", {}) == -1
        assert safe_eval("value > -5", {"value": 0}) is True

    def test_allowed_function_len(self):
        assert safe_eval("len(items)", {"items": [1, 2, 3]}) == 3

    def test_allowed_function_str(self):
        assert safe_eval("str(count)", {"count": 42}) == "42"

    def test_allowed_function_int(self):
        assert safe_eval("int(value)", {"value": "10"}) == 10

    def test_allowed_function_isinstance(self):
        assert safe_eval("isinstance(val, int)", {"val": 5}) is True
        assert safe_eval("isinstance(val, str)", {"val": 5}) is False

    def test_allowed_function_min_max(self):
        assert safe_eval("min(a, b)", {"a": 3, "b": 7}) == 3
        assert safe_eval("max(a, b)", {"a": 3, "b": 7}) == 7

    def test_allowed_function_abs(self):
        assert safe_eval("abs(n)", {"n": -5}) == 5

    def test_allowed_function_bool(self):
        assert safe_eval("bool(value)", {"value": ""}) is False
        assert safe_eval("bool(value)", {"value": "x"}) is True

    def test_allowed_function_sum(self):
        assert safe_eval("sum(items)", {"items": [1, 2, 3]}) == 6

    def test_none_comparison(self):
        assert safe_eval("value is None", {"value": None}) is True
        assert safe_eval("value is not None", {"value": 42}) is True

    def test_string_constants(self):
        assert safe_eval("'hello'", {}) == "hello"

    def test_boolean_constants(self):
        assert safe_eval("True", {}) is True
        assert safe_eval("False", {}) is False

    def test_slice_access(self):
        assert safe_eval("items[1:3]", {"items": [0, 1, 2, 3, 4]}) == [1, 2]


class TestSafeEvalBlockedExpressions:
    """Verify that dangerous expressions are rejected with SafeEvalError."""

    def test_import_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval("__import__('os')", {})

    def test_eval_not_in_allowed(self):
        with pytest.raises(SafeEvalError):
            safe_eval("eval('1+1')", {})

    def test_exec_not_in_allowed(self):
        with pytest.raises(SafeEvalError):
            safe_eval("exec('x=1')", {})

    def test_compile_not_in_allowed(self):
        with pytest.raises(SafeEvalError):
            safe_eval("compile('', '', 'exec')", {})

    def test_open_not_in_allowed(self):
        with pytest.raises(SafeEvalError):
            safe_eval("open('/etc/passwd')", {})

    def test_dunder_class_access(self):
        with pytest.raises(SafeEvalError, match="private/dunder attribute"):
            safe_eval("x.__class__", {"x": ""})

    def test_dunder_mro_traversal(self):
        with pytest.raises(SafeEvalError):
            safe_eval("().__class__.__bases__[0].__subclasses__()", {})

    def test_dunder_globals(self):
        with pytest.raises(SafeEvalError, match="private/dunder attribute"):
            safe_eval("x.__init__.__globals__", {"x": ""})

    def test_lambda_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval("(lambda x: x)(1)", {})

    def test_list_comprehension_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed expression type"):
            safe_eval("[x for x in items]", {"items": [1, 2]})

    def test_dict_comprehension_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed expression type"):
            safe_eval("{k: v for k, v in d.items()}", {"d": {"a": 1}})

    def test_set_comprehension_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed expression type"):
            safe_eval("{x for x in items}", {"items": [1, 2]})

    def test_set_unhashable_element_raises_safe_eval_error(self):
        with pytest.raises(SafeEvalError, match="not hashable"):
            safe_eval("{items}", {"items": [1, 2]})

    def test_set_membership_unhashable_left_raises_safe_eval_error(self):
        with pytest.raises(SafeEvalError, match="Membership test failed"):
            safe_eval("items in {1, 2, 3}", {"items": [1, 2]})

    def test_dict_unhashable_key_raises_safe_eval_error(self):
        with pytest.raises(SafeEvalError, match="not hashable"):
            safe_eval("{items: 1}", {"items": [1, 2]})

    def test_generator_expression_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed expression type"):
            safe_eval("sum(x for x in items)", {"items": [1, 2]})

    def test_walrus_operator_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed expression type"):
            safe_eval("(n := 10)", {})

    def test_bitwise_and_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed binary operator"):
            safe_eval("flags & 0xFF", {"flags": 255})

    def test_bitwise_or_blocked(self):
        with pytest.raises(SafeEvalError, match="Disallowed binary operator"):
            safe_eval("a | b", {"a": 1, "b": 2})

    def test_dunder_method_call_blocked(self):
        with pytest.raises(SafeEvalError, match="private/dunder method"):
            safe_eval("items.__delitem__(0)", {"items": [1, 2]})

    def test_undefined_name_raises(self):
        with pytest.raises(SafeEvalError, match="Undefined name"):
            safe_eval("undefined_var > 0", {})

    def test_os_system_payload(self):
        with pytest.raises(SafeEvalError):
            safe_eval("__import__('os').system('id')", {})

    def test_getattr_bypass_attempt(self):
        with pytest.raises(SafeEvalError):
            safe_eval("getattr(str, '__class__')", {})

    def test_type_function_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval("type(x)", {"x": 1})

    def test_starred_args_blocked(self):
        with pytest.raises(SafeEvalError, match="Starred arguments"):
            safe_eval("list(*args)", {"args": [1, 2]})

    def test_kwargs_unpacking_blocked(self):
        with pytest.raises(SafeEvalError, match="kwargs unpacking"):
            safe_eval("dict(**d)", {"d": {"a": 1}})

    def test_private_attribute_blocked(self):
        with pytest.raises(SafeEvalError, match="private/dunder attribute"):
            safe_eval("obj._private", {"obj": object()})

    def test_list_clear_mutation_blocked(self):
        items = [1, 2, 3]
        with pytest.raises(SafeEvalError, match="built-in types"):
            safe_eval("list.clear(items)", {"items": items})
        assert items == [1, 2, 3]

    def test_dict_update_mutation_blocked(self):
        state = {"key": "original"}
        with pytest.raises(SafeEvalError, match="built-in types"):
            safe_eval("dict.update(state, {'key': 'injected'})", {"state": state})
        assert state == {"key": "original"}

    def test_set_discard_mutation_blocked(self):
        with pytest.raises(SafeEvalError, match="built-in types"):
            safe_eval("set.discard(s, 1)", {"s": {1, 2, 3}})


class TestSafeEvalEdgeCases:
    """Edge cases and backwards compatibility tests."""

    def test_empty_string_raises(self):
        with pytest.raises(SafeEvalError, match="non-empty string"):
            safe_eval("", {})

    def test_whitespace_only_raises(self):
        with pytest.raises(SafeEvalError, match="non-empty string"):
            safe_eval("   ", {})

    def test_none_expression_raises(self):
        with pytest.raises(SafeEvalError, match="non-empty string"):
            safe_eval(None, {})

    def test_syntax_error_propagates(self):
        with pytest.raises(SyntaxError):
            safe_eval("===", {})

    def test_string_containing_import_as_value_allowed(self):
        result = safe_eval("status == '__import__'", {"status": "__import__"})
        assert result is True

    def test_string_containing_eval_as_value_allowed(self):
        result = safe_eval("cmd == 'eval'", {"cmd": "eval"})
        assert result is True

    def test_string_with_open_keyword_allowed(self):
        result = safe_eval("status == 'open'", {"status": "open"})
        assert result is True

    def test_zero_division_raises_exception(self):
        with pytest.raises(ZeroDivisionError):
            safe_eval("1 / 0", {})

    def test_local_vars_none_defaults_to_empty(self):
        assert safe_eval("42", None) == 42

    def test_complex_workflow_expression(self):
        vars = {
            "status": "success",
            "value": 75,
            "priority": "high",
        }
        result = safe_eval(
            "status == 'success' and value > 50 and priority in ['high', 'critical']",
            vars,
        )
        assert result is True

    def test_nested_subscript(self):
        data = {"items": [{"name": "first"}, {"name": "second"}]}
        assert safe_eval("data['items'][0]['name']", {"data": data}) == "first"
