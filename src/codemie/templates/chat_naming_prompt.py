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

chat_naming_prompt = """
Generate a short, specific title for this conversation based on the exchange below.

Rules:
- Return only the title text, nothing else — no quotes, no labels, no trailing punctuation.
- Maximum 60 characters.
- Be concrete: reference the actual topic, not a generic phrase like "Conversation" or "Chat".
- If the user message is vague, prefer the topic revealed in the assistant response over restating the user's wording.

User message:
{first_message}

Assistant response:
{assistant_response}
""".strip()
