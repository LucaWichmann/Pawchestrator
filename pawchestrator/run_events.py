"""In-memory run event queues for SSE streaming."""

from __future__ import annotations

import asyncio
from typing import Any

_run_stream_queues: dict[str, asyncio.Queue[object]] = {}
_STREAM_SENTINEL = object()


def get_or_create_run_queue(run_id: str) -> asyncio.Queue[object]:
    if run_id not in _run_stream_queues:
        _run_stream_queues[run_id] = asyncio.Queue()
    return _run_stream_queues[run_id]


async def push_run_event(run_id: str, event_type: str, data: dict[str, Any]) -> None:
    queue = _run_stream_queues.get(run_id)
    if queue is None:
        return
    await queue.put({"type": event_type, "data": data})


def close_run_stream(run_id: str) -> None:
    queue = _run_stream_queues.get(run_id)
    if queue is not None:
        queue.put_nowait(_STREAM_SENTINEL)
