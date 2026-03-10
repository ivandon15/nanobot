"""Image generation tool using OpenRouter."""

import base64
import uuid
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool


class GenerateImageTool(Tool):
    """Generate images using an OpenRouter image generation model."""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: str,
        workspace: Path,
    ):
        self._model = model
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text description. "
            "Returns the file path of the saved image — pass it to the `message` tool's `media` field to send it to the user."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate",
                    "minLength": 1,
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Optional: things to avoid in the image",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Optional: image aspect ratio",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                },
            },
            "required": ["prompt"],
        }

    async def execute(
        self,
        prompt: str,
        negative_prompt: str | None = None,
        aspect_ratio: str | None = None,
        **kwargs: Any,
    ) -> str:
        full_prompt = prompt
        if negative_prompt:
            full_prompt += f"\n\nAvoid: {negative_prompt}"
        if aspect_ratio:
            full_prompt += f"\n\nAspect ratio: {aspect_ratio}"

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": full_prompt}],
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        logger.info("Generating image with model {}: {}", self._model, prompt[:80])

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self._api_base}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except Exception as e:
            return f"Error: Failed to call image generation API: {e}"

        if response.status_code != 200:
            return f"Error: Image generation API returned {response.status_code}: {response.text[:200]}"

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            return f"Error: Unexpected API response format: {e}"

        # Extract base64 image from content parts
        image_b64 = None
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:image/"):
                        image_b64 = url.split(",", 1)[1] if "," in url else None
                    break
        elif isinstance(content, str) and content.startswith("data:image/"):
            image_b64 = content.split(",", 1)[1] if "," in content else None

        if not image_b64:
            return "Error: No image data found in API response. The model may not support image generation."

        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception as e:
            return f"Error: Failed to decode image data: {e}"

        out_dir = self._workspace / "generated_images"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{uuid.uuid4()}.png"
        out_path.write_bytes(image_bytes)

        logger.info("Image saved to {}", out_path)
        return str(out_path)
