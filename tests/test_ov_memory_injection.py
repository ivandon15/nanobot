import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from nanobot.agent.context import ContextBuilder


@pytest.mark.asyncio
async def test_build_messages_injects_ov_memory(tmp_path):
    builder = ContextBuilder(workspace=tmp_path, ov_data_path="/fake/path")

    mock_client = AsyncMock()
    mock_client.search_memory.return_value = {
        "results": [{"abstract": "User discussed paper B yesterday", "score": 0.9}]
    }

    with patch("nanobot.agent.context.get_ov_client", return_value=mock_client):
        msgs = await builder.build_messages(
            history=[], current_message="发小红书长文吧"
        )

    system_content = msgs[0]["content"]
    assert "Related Memories" in system_content
    assert "paper B" in system_content


@pytest.mark.asyncio
async def test_build_messages_no_ov_skips_injection(tmp_path):
    builder = ContextBuilder(workspace=tmp_path, ov_data_path=None)
    msgs = await builder.build_messages(history=[], current_message="hello")
    system_content = msgs[0]["content"]
    assert "Related Memories" not in system_content
