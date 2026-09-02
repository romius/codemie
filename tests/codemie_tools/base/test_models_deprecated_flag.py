# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from codemie_tools.base.models import Tool, ToolMetadata


def test_tool_metadata_deprecated_defaults_false():
    metadata = ToolMetadata(name="anything")
    dumped = metadata.model_dump()
    assert "deprecated" in dumped
    assert dumped["deprecated"] is False


def test_tool_metadata_deprecated_can_be_set_true():
    metadata = ToolMetadata(name="anything", deprecated=True)
    assert metadata.deprecated is True
    assert metadata.model_dump()["deprecated"] is True


def test_tool_carries_deprecated_from_metadata():
    metadata = ToolMetadata(name="anything", deprecated=True)
    tool = Tool.from_metadata(metadata)
    dumped = tool.model_dump()
    assert dumped["deprecated"] is True


def test_tool_deprecated_defaults_false():
    tool = Tool(name="anything")
    assert tool.model_dump()["deprecated"] is False
