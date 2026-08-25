from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import List, Optional
from pydantic import BaseModel
import asyncio
from sqlalchemy import select, delete, desc
from app.database.session import AsyncSessionLocal
from app.database.models import ChatMessageModel, BotModel
from app.database.queries import sync_channel_history

router = APIRouter(prefix="/api/chat", tags=["Chat"])

@router.get("")
async def get_chat_history(
    bot_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    limit: int = Query(50, le=200)
):
    async with AsyncSessionLocal() as session:
        stmt = select(ChatMessageModel).order_by(desc(ChatMessageModel.timestamp))
        if channel_id:
            stmt = stmt.where(ChatMessageModel.channel_id == str(channel_id))
        stmt = stmt.limit(limit)
        
        res = await session.execute(stmt)
        messages = res.scalars().all()
        return [m.to_dict() for m in messages]

@router.delete("/{message_id}")
async def delete_chat_message(message_id: str):
    async with AsyncSessionLocal() as session:
        stmt = delete(ChatMessageModel).where(ChatMessageModel.id == str(message_id))
        result = await session.execute(stmt)
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Message not found")
        return {"status": "success"}

@router.get("/channels")
async def get_chat_channels(bot_id: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        stmt = select(
            ChatMessageModel.channel_id,
            ChatMessageModel.channel_name
        ).distinct()
        res = await session.execute(stmt)
        channels = res.all()
        return [{"channel_id": row[0], "channel_name": row[1]} for row in channels]

class SyncRequest(BaseModel):
    bot_id: str
    channel_id: str
    limit: int = 100

@router.post("/sync")
async def sync_chat_history(req: SyncRequest):
    # This requires reaching out to the running bot instance to fetch discord channel history
    from app.bot.bot_manager import bot_manager
    
    bot_instance = bot_manager.active_instances.get(req.bot_id)
    if not bot_instance or bot_instance.status != "running":
        raise HTTPException(status_code=400, detail="Bot is not running")
        
    try:
        channel = bot_instance.bot_client.get_channel(int(req.channel_id))
        if not channel or not hasattr(channel, "history"):
            raise HTTPException(status_code=400, detail="Channel not found or not a text channel")
            
        recent_msgs = []
        async for msg in channel.history(limit=req.limit):
            recent_msgs.append({
                "id": str(msg.id),
                "content": msg.content,
                "author_id": str(msg.author.id),
                "author_name": msg.author.display_name or msg.author.name,
                "channel_name": getattr(channel, "name", "DM"),
                "has_attachments": len(msg.attachments) > 0,
                "timestamp": msg.created_at.replace(tzinfo=None)
            })
            
        await sync_channel_history(str(req.channel_id), recent_msgs, fetch_limit=req.limit)
        return {"status": "success", "synced_count": len(recent_msgs)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
