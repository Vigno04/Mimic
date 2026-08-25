import uuid
import json
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete, update
from app.database.models import BotModel
from app.database.session import get_db, AsyncSession
from app.bot.bot_manager import bot_manager

router = APIRouter(prefix="/api/bots", tags=["bots"])

class BotCreateOrUpdate(BaseModel):
    name: str
    discord_token: str
    endpoint_chain: List[str] = []
    system_prompt: str = "You are a friendly and intelligent AI assistant."
    triggers: Optional[List[Dict[str, Any]]] = []
    enabled_channels: List[str] = []
    blacklisted_channels: List[str] = []
    blacklisted_users: List[str] = []
    trigger_keywords: List[str] = []
    case_sensitive: bool = False
    trigger_mode: str = "keywords"  # (legacy)
    reply_policy: str = "ai_choice"  # (legacy)
    memory_mode: str = "recent_active"  # 'full', 'recent_active', 'tool_only'
    active_users_count: int = 5
    recent_messages_count: int = 15
    cooldown_seconds: int = 3
    ignore_bots: bool = True
    max_consecutive_bot_replies: int = 1

@router.get("")
@router.get("/")
async def list_bots(db: AsyncSession = Depends(get_db)):
    stmt = select(BotModel).order_by(BotModel.name)
    res = await db.execute(stmt)
    bots = res.scalars().all()
    result = []
    for b in bots:
        d = b.to_dict()
        # Merge live status
        live_status = bot_manager.get_status(b.id)
        d["live_status"] = live_status
        result.append(d)
    return result

@router.post("")
@router.post("/")
async def create_bot(payload: BotCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    clean_token = payload.discord_token.strip().strip('"\'')
    if clean_token.lower().startswith("bot "):
        clean_token = clean_token[4:].strip()
        
    bot = BotModel(
        id=str(uuid.uuid4()),
        name=payload.name.strip(),
        discord_token=clean_token,
        endpoint_chain=json.dumps(payload.endpoint_chain),
        system_prompt=payload.system_prompt,
        triggers=json.dumps(payload.triggers or []),
        enabled_channels=json.dumps(payload.enabled_channels),
        blacklisted_channels=json.dumps(payload.blacklisted_channels),
        blacklisted_users=json.dumps(payload.blacklisted_users),
        trigger_keywords=json.dumps(payload.trigger_keywords),
        case_sensitive=payload.case_sensitive,
        trigger_mode=payload.trigger_mode,
        reply_policy=payload.reply_policy,
        memory_mode=payload.memory_mode,
        active_users_count=payload.active_users_count,
        recent_messages_count=payload.recent_messages_count,
        cooldown_seconds=payload.cooldown_seconds,
        ignore_bots=payload.ignore_bots,
        max_consecutive_bot_replies=payload.max_consecutive_bot_replies,
        is_running=False
    )
    db.add(bot)
    await db.commit()
    await db.refresh(bot)
    return bot.to_dict()

@router.get("/{bot_id}")
async def get_bot(bot_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(BotModel).where(BotModel.id == bot_id)
    res = await db.execute(stmt)
    bot = res.scalars().first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found.")
    d = bot.to_dict()
    d["live_status"] = bot_manager.get_status(bot.id)
    return d

@router.put("/{bot_id}")
async def update_bot(bot_id: str, payload: BotCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(BotModel).where(BotModel.id == bot_id)
    res = await db.execute(stmt)
    bot = res.scalars().first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found.")

    clean_token = payload.discord_token.strip().strip('"\'')
    if clean_token.lower().startswith("bot "):
        clean_token = clean_token[4:].strip()

    bot.name = payload.name.strip()
    bot.discord_token = clean_token
    bot.endpoint_chain = json.dumps(payload.endpoint_chain)
    bot.system_prompt = payload.system_prompt
    if payload.triggers is not None:
        bot.triggers = json.dumps(payload.triggers)
    bot.enabled_channels = json.dumps(payload.enabled_channels)
    bot.blacklisted_channels = json.dumps(payload.blacklisted_channels)
    bot.blacklisted_users = json.dumps(payload.blacklisted_users)
    bot.trigger_keywords = json.dumps(payload.trigger_keywords)
    bot.case_sensitive = payload.case_sensitive
    bot.trigger_mode = payload.trigger_mode
    bot.reply_policy = payload.reply_policy
    bot.memory_mode = payload.memory_mode
    bot.active_users_count = payload.active_users_count
    bot.recent_messages_count = payload.recent_messages_count
    bot.cooldown_seconds = payload.cooldown_seconds
    bot.ignore_bots = payload.ignore_bots
    bot.max_consecutive_bot_replies = payload.max_consecutive_bot_replies

    await db.commit()
    await db.refresh(bot)
    
    # If running, update instance config live
    if bot_id in bot_manager.active_instances:
        bot_manager.active_instances[bot_id].update_config(bot.to_dict())

    return bot.to_dict()

@router.delete("/{bot_id}")
async def delete_bot(bot_id: str, db: AsyncSession = Depends(get_db)):
    await bot_manager.stop_bot(bot_id)
    stmt = delete(BotModel).where(BotModel.id == bot_id)
    res = await db.execute(stmt)
    await db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bot not found.")
    return {"status": "success", "message": "Bot deleted successfully."}

@router.post("/{bot_id}/start")
async def start_bot_endpoint(bot_id: str):
    return await bot_manager.start_bot(bot_id)

@router.post("/{bot_id}/stop")
async def stop_bot_endpoint(bot_id: str):
    return await bot_manager.stop_bot(bot_id)

@router.post("/{bot_id}/restart")
async def restart_bot_endpoint(bot_id: str):
    return await bot_manager.restart_bot(bot_id)
