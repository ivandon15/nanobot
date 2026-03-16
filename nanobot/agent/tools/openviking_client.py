"""VikingClient: thin async wrapper around per-path LocalClient instances.

We use LocalClient directly instead of AsyncOpenViking because AsyncOpenViking
is a class-level singleton that ignores the path argument after first init,
which causes LOCK conflicts when multiple agents use different data paths.
"""
from __future__ import annotations

import threading
from pathlib import Path

from loguru import logger

_clients: dict[str, object] = {}
_thread_lock = threading.Lock()


async def get_client(data_path: str):
    """Return the initialized LocalClient for data_path, creating it if needed."""
    path = str(Path(data_path).expanduser())
    if path in _clients:
        return _clients[path]
    with _thread_lock:
        if path in _clients:
            return _clients[path]
        from openviking.client.local import LocalClient  # type: ignore[import]
        client = LocalClient(path=path)
        await client.initialize()
        _clients[path] = client
        logger.info("OpenViking initialized at {}", path)
    return _clients[path]
