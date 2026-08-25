import json
import datetime
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete
from app.database.models import (
    EndpointModel,
    BotModel,
    ServerMemoryModel,
    UserMemoryModel,
    ChatMessageModel,
    AuditLogModel
)
from app.database.session import get_db, AsyncSession

router = APIRouter(prefix="/api/backup", tags=["backup"])

@router.get("/export")
async def export_database_json(db: AsyncSession = Depends(get_db)):
    endpoints = (await db.execute(select(EndpointModel))).scalars().all()
    bots = (await db.execute(select(BotModel))).scalars().all()
    server_mem = (await db.execute(select(ServerMemoryModel))).scalars().all()
    user_mem = (await db.execute(select(UserMemoryModel))).scalars().all()
    messages = (await db.execute(select(ChatMessageModel))).scalars().all()
    audits = (await db.execute(select(AuditLogModel))).scalars().all()

    now = datetime.datetime.now(datetime.timezone.utc)
    export_data = {
        "version": "1.0.0",
        "exported_at": now.isoformat(),
        "endpoints": [e.to_dict() for e in endpoints],
        "bots": [b.to_dict() for b in bots],
        "server_memories": [sm.to_dict() for sm in server_mem],
        "user_memories": [um.to_dict() for um in user_mem],
        "chat_messages": [cm.to_dict() for cm in messages],
        "audit_logs": [a.to_dict() for a in audits]
    }
    
    headers = {
        "Content-Disposition": f"attachment; filename=mimic_backup_{now.strftime('%Y%m%d_%H%M%S')}.json"
    }
    return JSONResponse(content=export_data, headers=headers)

@router.post("/import")
async def import_database_json(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")

    imported_counts = {"endpoints": 0, "bots": 0, "server_memories": 0, "user_memories": 0}

    # Import Endpoints
    if "endpoints" in data:
        for item in data["endpoints"]:
            stmt = select(EndpointModel).where(EndpointModel.id == item["id"])
            existing = (await db.execute(stmt)).scalars().first()
            if not existing:
                ep = EndpointModel(
                    id=item["id"],
                    name=item["name"],
                    provider=item.get("provider", "openai"),
                    base_url=item.get("base_url"),
                    api_key=item.get("api_key"),
                    model_name=item["model_name"],
                    is_global_fallback=item.get("is_global_fallback", False)
                )
                db.add(ep)
                imported_counts["endpoints"] += 1

    # Import Bots
    if "bots" in data:
        for item in data["bots"]:
            stmt = select(BotModel).where(BotModel.id == item["id"])
            existing = (await db.execute(stmt)).scalars().first()
            if not existing:
                bot = BotModel(
                    id=item["id"],
                    name=item["name"],
                    discord_token=item.get("discord_token", ""),
                    endpoint_chain=json.dumps(item.get("endpoint_chain", [])),
                    system_prompt=item.get("system_prompt", ""),
                    triggers=json.dumps(item.get("triggers", [])),
                    enabled_channels=json.dumps(item.get("enabled_channels", [])),
                    blacklisted_channels=json.dumps(item.get("blacklisted_channels", [])),
                    blacklisted_users=json.dumps(item.get("blacklisted_users", [])),
                    trigger_keywords=json.dumps(item.get("trigger_keywords", [])),
                    case_sensitive=item.get("case_sensitive", False),
                    trigger_mode=item.get("trigger_mode", "keywords"),
                    reply_policy=item.get("reply_policy", "ai_choice"),
                    memory_mode=item.get("memory_mode", "recent_active"),
                    active_users_count=item.get("active_users_count", 5),
                    recent_messages_count=item.get("recent_messages_count", 15),
                    cooldown_seconds=item.get("cooldown_seconds", 3),
                    ignore_bots=item.get("ignore_bots", True),
                    max_consecutive_bot_replies=item.get("max_consecutive_bot_replies", 1),
                    is_running=False
                )
                db.add(bot)
                imported_counts["bots"] += 1

    # Import Server Memories
    if "server_memories" in data:
        for item in data["server_memories"]:
            stmt = select(ServerMemoryModel).where(ServerMemoryModel.id == item["id"])
            existing = (await db.execute(stmt)).scalars().first()
            if not existing:
                sm = ServerMemoryModel(
                    id=item["id"],
                    bot_id=item.get("bot_id"),
                    guild_id=item.get("guild_id"),
                    key_phrase=item["key_phrase"],
                    fact=item["fact"],
                    category=item.get("category", "general")
                )
                db.add(sm)
                imported_counts["server_memories"] += 1

    # Import User Memories
    if "user_memories" in data:
        for item in data["user_memories"]:
            stmt = select(UserMemoryModel).where(UserMemoryModel.id == item["id"])
            existing = (await db.execute(stmt)).scalars().first()
            if not existing:
                um = UserMemoryModel(
                    id=item["id"],
                    bot_id=item.get("bot_id"),
                    user_id=item["user_id"],
                    username=item.get("username", "Unknown"),
                    fact=item["fact"],
                    category=item.get("category", "general")
                )
                db.add(um)
                imported_counts["user_memories"] += 1

    await db.commit()
    return {"status": "success", "imported": imported_counts}
