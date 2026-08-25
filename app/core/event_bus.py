"""
In-memory async event bus for streaming LLM events to dashboard SSE clients.

Events are published by the LLM client during streaming and consumed by
SSE-connected browser clients via /api/logs/stream.

Event types:
  - stream_start:      New LLM request started
  - text_delta:        A chunk of generated text arrived
  - tool_call_start:   LLM requested a tool call
  - tool_call_result:  Tool execution completed
  - stream_end:        Request fully completed
"""

import asyncio
import json
import time
import uuid
import logging
from typing import Dict, Any, Optional, Set

logger = logging.getLogger(__name__)


class StreamEvent:
    """A single streaming event."""
    def __init__(self, event_type: str, data: Dict[str, Any], request_id: str):
        self.event_type = event_type
        self.data = data
        self.request_id = request_id
        self.timestamp = time.time()

    def to_sse(self) -> str:
        """Format as Server-Sent Event string."""
        payload = {
            "type": self.event_type,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            **self.data
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class EventBus:
    """Simple in-memory pub/sub for streaming events."""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        logger.debug(f"SSE client subscribed. Total: {len(self._subscribers)}")
        return queue

    async def unsubscribe(self, queue: asyncio.Queue):
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(queue)
        logger.debug(f"SSE client unsubscribed. Total: {len(self._subscribers)}")

    async def publish(self, event: StreamEvent):
        """Publish an event to all subscribers."""
        async with self._lock:
            dead_queues = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead_queues.append(q)
            for q in dead_queues:
                self._subscribers.discard(q)

    def has_subscribers(self) -> bool:
        return len(self._subscribers) > 0

    @staticmethod
    def new_request_id() -> str:
        return str(uuid.uuid4())[:8]


# Global singleton
event_bus = EventBus()
