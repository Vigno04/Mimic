import asyncio
import json
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from typing import Optional
from sqlalchemy import select, desc
from app.database.models import AuditLogModel
from app.database.session import AsyncSessionLocal
from app.core.event_bus import event_bus

router = APIRouter(prefix="/api/logs", tags=["Logs"])

@router.get("")
async def get_logs(
    bot_id: Optional[str] = None,
    limit: int = Query(10, description="Number of logs to fetch")
):
    async with AsyncSessionLocal() as session:
        stmt = select(AuditLogModel).order_by(desc(AuditLogModel.timestamp))
        if bot_id:
            stmt = stmt.where(AuditLogModel.bot_id == bot_id)
        
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        logs = result.scalars().all()
        return [log.to_dict() for log in logs]


@router.get("/stream")
async def stream_logs():
    """SSE endpoint for live streaming LLM events to the dashboard."""
    async def event_generator():
        queue = await event_bus.subscribe()
        try:
            # Send a keep-alive comment immediately
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    # Send keep-alive ping
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
