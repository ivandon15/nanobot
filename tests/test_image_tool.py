import pytest
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_generate_image_saves_file(tmp_path):
    from nanobot.agent.tools.image import GenerateImageTool

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    b64 = base64.b64encode(fake_png).decode()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                ]
            }
        }]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    tool = GenerateImageTool(
        model="google/gemini-2.0-flash-exp:image",
        api_key="test-key",
        api_base="https://openrouter.ai/api",
        workspace=tmp_path,
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(prompt="a red cat")

    assert "generated_images" in result
    assert result.endswith(".png")
    saved = Path(result)
    assert saved.exists()
    assert saved.read_bytes() == fake_png


@pytest.mark.asyncio
async def test_generate_image_api_error(tmp_path):
    from nanobot.agent.tools.image import GenerateImageTool

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    tool = GenerateImageTool(
        model="google/gemini-2.0-flash-exp:image",
        api_key="test-key",
        api_base="https://openrouter.ai/api",
        workspace=tmp_path,
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(prompt="a red cat")

    assert result.startswith("Error")


@pytest.mark.asyncio
async def test_generate_image_no_image_in_response(tmp_path):
    from nanobot.agent.tools.image import GenerateImageTool

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": [{"type": "text", "text": "I cannot generate images"}]}}]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    tool = GenerateImageTool(
        model="google/gemini-2.0-flash-exp:image",
        api_key="test-key",
        api_base="https://openrouter.ai/api",
        workspace=tmp_path,
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(prompt="a red cat")

    assert result.startswith("Error")
