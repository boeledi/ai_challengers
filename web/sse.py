"""Server-Sent Events (SSE) utilities for streaming pipeline progress."""

import asyncio
import json
from typing import AsyncGenerator


async def event_stream(queue: asyncio.Queue) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events from a queue.

    Events are dicts with 'type' and 'data' keys.
    A None sentinel closes the stream. Pipelines emit explicit terminal events
    such as "complete", "canceled", or "error" before the sentinel.
    """
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            # Send keepalive comment to prevent connection timeout
            yield ": keepalive\n\n"
            continue

        if event is None:
            break

        event_type = event.get("type", "progress")
        event_data = event.get("data", {})
        yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"


def make_event(event_type: str, **kwargs) -> dict:
    """Create a standardized SSE event dict."""
    return {"type": event_type, "data": kwargs}
