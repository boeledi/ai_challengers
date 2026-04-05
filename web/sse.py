"""Server-Sent Events (SSE) utilities for streaming pipeline progress."""

import asyncio
import json
from typing import AsyncGenerator


async def event_stream(queue: asyncio.Queue) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events from a queue.

    Events are dicts with 'type' and 'data' keys.
    A None sentinel signals stream completion.
    """
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            # Send keepalive comment to prevent connection timeout
            yield ": keepalive\n\n"
            continue

        if event is None:
            # Send completion event and end stream
            yield f"event: complete\ndata: {json.dumps({'status': 'done'})}\n\n"
            break

        event_type = event.get("type", "progress")
        event_data = event.get("data", {})
        yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"


def make_event(event_type: str, **kwargs) -> dict:
    """Create a standardized SSE event dict."""
    return {"type": event_type, "data": kwargs}
