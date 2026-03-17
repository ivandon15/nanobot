"""Tests for filesystem tools."""

import pytest
from pathlib import Path

from nanobot.agent.tools.filesystem import ReadFileTool


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_read_file_truncates_large_file(tmp_workspace: Path) -> None:
    """ReadFileTool should truncate files exceeding MAX_CHARS instead of reading all into memory."""
    large_file = tmp_workspace / "big.txt"
    # Write content larger than the 128_000 char limit
    large_file.write_text("x" * 200_000, encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_workspace)
    result = await tool.execute(path="big.txt")

    assert len(result) < 200_000
    assert "truncated" in result.lower()


@pytest.mark.asyncio
async def test_read_file_small_file_unchanged(tmp_workspace: Path) -> None:
    """ReadFileTool should return full content for files under the limit."""
    small_file = tmp_workspace / "small.txt"
    small_file.write_text("hello world", encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_workspace)
    result = await tool.execute(path="small.txt")

    assert result == "hello world"
