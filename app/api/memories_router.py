import uuid
import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, delete, update, desc
from app.database.models import ServerMemoryModel, UserMemoryModel, DiscordUserModel
from app.database.session import get_db, AsyncSession
from app.database.queries import (
    wipe_user_memories,
    get_user_memories,
    get_user_memories_grouped_by_user,
    list_discord_users,
    save_or_update_user_memory
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])

# --- Schemas ---

class ServerMemoryCreateOrUpdate(BaseModel):
    bot_id: Optional[str] = None
    guild_id: Optional[str] = None
    key_phrase: str
    fact: str
    category: str = "general"

class UserMemoryCreateOrUpdate(BaseModel):
    bot_id: Optional[str] = None
    user_id: str
    username: str
    fact: str
    category: str = "general"

# --- Server Memories ---

@router.get("/server")
async def list_server_memories(
    bot_id: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(ServerMemoryModel)
    if bot_id:
        stmt = stmt.where((ServerMemoryModel.bot_id == bot_id) | (ServerMemoryModel.bot_id == None))
    if category:
        stmt = stmt.where(ServerMemoryModel.category == category)
    if search:
        stmt = stmt.where(
            (ServerMemoryModel.key_phrase.ilike(f"%{search}%")) |
            (ServerMemoryModel.fact.ilike(f"%{search}%"))
        )
    stmt = stmt.order_by(desc(ServerMemoryModel.updated_at))
    res = await db.execute(stmt)
    return [m.to_dict() for m in res.scalars().all()]

@router.post("/server")
async def create_server_memory(payload: ServerMemoryCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    mem = ServerMemoryModel(
        id=str(uuid.uuid4()),
        bot_id=payload.bot_id,
        guild_id=payload.guild_id,
        key_phrase=payload.key_phrase,
        fact=payload.fact,
        category=payload.category
    )
    db.add(mem)
    await db.commit()
    await db.refresh(mem)
    return mem.to_dict()

@router.put("/server/{memory_id}")
async def update_server_memory(memory_id: str, payload: ServerMemoryCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(ServerMemoryModel).where(ServerMemoryModel.id == memory_id)
    res = await db.execute(stmt)
    mem = res.scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="Server memory not found.")
    mem.bot_id = payload.bot_id
    mem.guild_id = payload.guild_id
    mem.key_phrase = payload.key_phrase
    mem.fact = payload.fact
    mem.category = payload.category
    await db.commit()
    await db.refresh(mem)
    return mem.to_dict()

@router.delete("/server/{memory_id}")
async def delete_server_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    stmt = delete(ServerMemoryModel).where(ServerMemoryModel.id == memory_id)
    res = await db.execute(stmt)
    await db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Server memory not found.")
    return {"status": "success", "message": "Server memory deleted."}

# --- User Memories ---

@router.get("/user")
async def list_user_memories(
    bot_id: Optional[str] = None,
    user_id: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None
):
    memories = await get_user_memories(
        bot_id=bot_id,
        user_ids=[user_id] if user_id else None,
        search=search
    )
    if category:
        memories = [m for m in memories if m.get("category") == category]
    return memories

@router.get("/user/grouped")
async def list_user_memories_grouped(
    bot_id: Optional[str] = None,
    search: Optional[str] = None
):
    """Returns all user memories grouped per user with full profile metadata for user card display."""
    return await get_user_memories_grouped_by_user(bot_id=bot_id, search=search)

@router.get("/directory/users")
async def list_all_discord_users(
    search: Optional[str] = None
):
    """Returns registered Discord users in the database."""
    return await list_discord_users(search=search)

@router.post("/directory/sync")
async def trigger_discord_member_sync():
    """Forces synchronization of all Discord server members from all connected bots into the database."""
    from app.bot.bot_manager import bot_manager
    synced_total = 0
    for bot_id, instance in bot_manager.active_instances.items():
        if instance.status == "running" and instance.bot_client and instance.bot_client.is_ready():
            for guild in instance.bot_client.guilds:
                try:
                    async for member in guild.fetch_members(limit=1000):
                        avatar_url = str(member.display_avatar.url) if hasattr(member, "display_avatar") and member.display_avatar else None
                        global_name = getattr(member, "global_name", None)
                        await upsert_discord_user(
                            user_id=str(member.id),
                            username=member.name,
                            global_name=global_name,
                            display_name=member.display_name or global_name or member.name,
                            avatar_url=avatar_url,
                            is_bot=member.bot
                        )
                        synced_total += 1
                except Exception as e:
                    logger.warning(f"Error syncing members for guild {guild.id}: {e}")
    return {"status": "success", "synced_members_count": synced_total}

@router.post("/user")
async def create_user_memory(payload: UserMemoryCreateOrUpdate):
    return await save_or_update_user_memory(
        user_id=payload.user_id,
        username=payload.username,
        fact=payload.fact,
        category=payload.category,
        bot_id=payload.bot_id
    )

@router.put("/user/{memory_id}")
async def update_user_memory(memory_id: str, payload: UserMemoryCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(UserMemoryModel).where(UserMemoryModel.id == memory_id)
    res = await db.execute(stmt)
    mem = res.scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="User memory not found.")
    mem.bot_id = payload.bot_id
    mem.user_id = payload.user_id
    mem.username = payload.username
    mem.fact = payload.fact
    mem.category = payload.category
    await db.commit()
    await db.refresh(mem)
    return mem.to_dict()

@router.delete("/user/{memory_id}")
async def delete_user_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    stmt = delete(UserMemoryModel).where(UserMemoryModel.id == memory_id)
    res = await db.execute(stmt)
    await db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="User memory not found.")
    return {"status": "success", "message": "User memory deleted."}

@router.delete("/user/wipe/{user_id}")
async def wipe_user_memories_endpoint(user_id: str, bot_id: Optional[str] = None):
    count = await wipe_user_memories(user_id=user_id, bot_id=bot_id)
    return {"status": "success", "deleted_count": count}
