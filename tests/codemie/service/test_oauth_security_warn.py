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

"""The insecure-storage startup warning must cover every enabled OAuth provider, not only GitLab."""

import codemie.service.oauth_security as oauth_security


def test_warns_for_each_enabled_provider(monkeypatch):
    warned = []
    monkeypatch.setattr(oauth_security, "warn_if_insecure_token_storage", lambda label: warned.append(label))
    monkeypatch.setattr(oauth_security.config, "GITLAB_OAUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(oauth_security.config, "JIRA_OAUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(oauth_security.config, "CONFLUENCE_OAUTH_ENABLED", True, raising=False)

    oauth_security.warn_insecure_oauth_storage_for_enabled_providers()
    assert warned == ["GitLab", "Jira", "Confluence"]


def test_warns_only_for_enabled_providers(monkeypatch):
    warned = []
    monkeypatch.setattr(oauth_security, "warn_if_insecure_token_storage", lambda label: warned.append(label))
    monkeypatch.setattr(oauth_security.config, "GITLAB_OAUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(oauth_security.config, "JIRA_OAUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(oauth_security.config, "CONFLUENCE_OAUTH_ENABLED", True, raising=False)

    oauth_security.warn_insecure_oauth_storage_for_enabled_providers()
    assert warned == ["Confluence"]
