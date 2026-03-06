"""VikingClient: thin async wrapper around the AsyncOpenViking singleton."""
from __future__ import annotations

import threading
from pathlib import Path

from loguru import logger

_client = None
_thread_lock = threading.Lock()


async def get_client(data_path: str):
    """Return the initialized AsyncOpenViking singleton, creating it if needed."""
    global _client
    if _client is not None:
        return _client
    with _thread_lock:
        if _client is not None:
            return _client
        from openviking import AsyncOpenViking  # type: ignore[import]
        path = str(Path(data_path).expanduser())
        _client = AsyncOpenViking(path=path)
        await _client.initialize()
        logger.info("OpenViking initialized at {}", path)
    return _client
