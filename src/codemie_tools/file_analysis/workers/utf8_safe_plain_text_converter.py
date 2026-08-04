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

"""UTF-8-safe override for MarkItDown's PlainTextConverter.

MarkItDown detects charset by reading only the first 4 KiB of the stream. When a
UTF-8 file starts with pure ASCII bytes, charset_normalizer may return 'ascii'.
PlainTextConverter then calls decode('ascii') on the full content, which raises
UnicodeDecodeError for any non-ASCII bytes (e.g. Cyrillic at position >4096).

This subclass catches that error and falls back to UTF-8 → full-content
charset detection → replacement decoding so Cyrillic and other multibyte UTF-8
content never causes a 500.
"""

from __future__ import annotations

from typing import Any, BinaryIO

from charset_normalizer import from_bytes
from markitdown import DocumentConverterResult, StreamInfo
from markitdown.converters import PlainTextConverter


class Utf8SafePlainTextConverter(PlainTextConverter):
    """PlainTextConverter that handles UTF-8 Cyrillic content when charset detection returns 'ascii'."""

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        data = file_stream.read()

        if stream_info.charset:
            try:
                return DocumentConverterResult(markdown=data.decode(stream_info.charset))
            except UnicodeDecodeError:
                pass  # Fall through to UTF-8 and full-content detection

        # UTF-8 handles the majority of modern files including Cyrillic
        try:
            return DocumentConverterResult(markdown=data.decode("utf-8"))
        except UnicodeDecodeError:
            pass

        # Run charset_normalizer on the full content (not just the first 4 KiB)
        result = from_bytes(data).best()
        if result is not None:
            return DocumentConverterResult(markdown=str(result))

        # Last resort: replace undecodable bytes rather than raising
        return DocumentConverterResult(markdown=data.decode("utf-8", errors="replace"))
