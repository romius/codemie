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

"""Service tests for project budget validation with zero-allocation categories."""

from types import SimpleNamespace

import pytest

from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.core.exceptions import ExtendedHTTPException


class TestValidateGroupCategoriesWithZeroPct:
    """Test _validate_group_categories accepts zero-pct non-platform categories."""

    def test_platform_only_passes_validation(self):
        """Platform 100%, CLI 0%, Premium Models 0% should pass validation."""
        categories = {
            "platform": SimpleNamespace(pct=100.0),
            "cli": SimpleNamespace(pct=0.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        # Should not raise
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_two_categories_with_zero_passes_validation(self):
        """Platform 60%, CLI 40%, Premium Models 0% should pass validation."""
        categories = {
            "platform": SimpleNamespace(pct=60.0),
            "cli": SimpleNamespace(pct=40.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        # Should not raise
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_platform_zero_passes_validation(self):
        """Platform 0%, CLI 100%, Premium Models 0% should pass (Platform can be 0%)."""
        categories = {
            "platform": SimpleNamespace(pct=0.0),
            "cli": SimpleNamespace(pct=100.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        # Should not raise
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_sum_not_100_fails_validation(self):
        """Platform 50%, CLI 30%, Premium Models 0% should fail (sum = 80%)."""
        categories = {
            "platform": SimpleNamespace(pct=50.0),
            "cli": SimpleNamespace(pct=30.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
        assert exc_info.value.code == 400
        assert "100" in exc_info.value.message

    def test_platform_missing_fails_validation(self):
        """Missing platform category should fail validation."""
        categories = {
            "cli": SimpleNamespace(pct=50.0),
            "premium_models": SimpleNamespace(pct=50.0),
        }

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
        assert exc_info.value.code == 400
        assert "platform" in exc_info.value.message.lower()


@pytest.mark.parametrize(
    "case,offending_key,categories",
    [
        (
            "negative",
            "cli",
            {
                "platform": SimpleNamespace(pct=50.0),
                "cli": SimpleNamespace(pct=-10.0),
            },
        ),
        (
            "nan",
            "platform",
            {
                "platform": SimpleNamespace(pct=float("nan")),
                "cli": SimpleNamespace(pct=0.0),
            },
        ),
        (
            "just_below_zero",
            "cli",
            {
                "platform": SimpleNamespace(pct=50.0),
                "cli": SimpleNamespace(pct=-0.001),
            },
        ),
    ],
)
def test_system_rejects_invalid_distribution_values(case, offending_key, categories):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
    assert exc_info.value.code == 400
    assert offending_key in exc_info.value.message
