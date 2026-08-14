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

"""Stale datasource detection configuration."""

from __future__ import annotations


# Advisory lock ID for stale datasource scheduler.
# Full lock registry: CA=987654321, Spend=987654322/323/324, LB=987654325, Activity=987654326, Rotation=987654327
STALE_DATASOURCE_LOCK_ID = 987654328
