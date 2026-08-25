import datetime
import uuid
import json
from typing import List, Dict, Any, Optional
import aiosqlite
from sqlalchemy import select, delete, update, func, desc
from app.core.config import settings
from app.database.models import (
    EndpointModel,
    BotModel,
    ServerMemoryModel,
    DiscordUserModel,
    UserMemoryModel,
    ChatMessageModel,
    AuditLogModel
)
from app.database.session import AsyncSessionLocal, get_db_file_path

def get_utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

# --- Chat Messages & FTS5 ---

async def log_chat_message(
    channel_id: str,
    channel_name: str,
    author_id: str,
    author_name: str,
    content: str,
    has_attachments: bool = False,
    message_id: Optional[str] = None,
    reference_message_id: Optional[str] = None,
    is_reply: bool = False,
    reactions: Optional[List[str]] = None
) -> ChatMessageModel:
    msg_id = message_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        msg = ChatMessageModel(
            id=msg_id,
            channel_id=str(channel_id),
            channel_name=channel_name or "unknown",
            author_id=str(author_id),
            author_name=author_name or "unknown",
            content=content,
            has_attachments=has_attachments,
            reference_message_id=reference_message_id,
            is_reply=is_reply,
            reactions=json.dumps(reactions or []),
            timestamp=get_utc_now()
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg

async def update_chat_message(message_id: str, new_content: str):
    async with AsyncSessionLocal() as session:
        stmt = update(ChatMessageModel).where(ChatMessageModel.id == str(message_id)).values(content=new_content)
        await session.execute(stmt)
        await session.commit()

async def delete_chat_message_by_discord_id(message_id: str):
    async with AsyncSessionLocal() as session:
        stmt = delete(ChatMessageModel).where(ChatMessageModel.id == str(message_id))
        await session.execute(stmt)
        await session.commit()

async def update_chat_message_reactions(message_id: str, reactions: List[str]):
    async with AsyncSessionLocal() as session:
        stmt = update(ChatMessageModel).where(ChatMessageModel.id == str(message_id)).values(reactions=json.dumps(reactions))
        await session.execute(stmt)
        await session.commit()

async def sync_channel_history(channel_id: str, discord_messages: List[Dict[str, Any]], fetch_limit: int = 100):
    """
    Synchronizes the local database with a list of messages from Discord.
    Expects discord_messages to be a list of dicts with: id, content, author_id, author_name, timestamp.
    """
    async with AsyncSessionLocal() as session:
        # Get all local messages for this channel
        stmt = select(ChatMessageModel).where(ChatMessageModel.channel_id == str(channel_id))
        res = await session.execute(stmt)
        local_msgs = {msg.id: msg for msg in res.scalars().all()}
        
        discord_ids = set()
        
        for d_msg in discord_messages:
            msg_id = str(d_msg["id"])
            discord_ids.add(msg_id)
            
            if msg_id in local_msgs:
                # Update content if it changed
                if local_msgs[msg_id].content != d_msg["content"]:
                    local_msgs[msg_id].content = d_msg["content"]
                    
                local_msgs[msg_id].reference_message_id = d_msg.get("reference_message_id")
                local_msgs[msg_id].is_reply = d_msg.get("is_reply", False)
                local_msgs[msg_id].reactions = json.dumps(d_msg.get("reactions", []))
            else:
                # Insert new message
                new_msg = ChatMessageModel(
                    id=msg_id,
                    channel_id=str(channel_id),
                    channel_name=d_msg.get("channel_name", "unknown"),
                    author_id=str(d_msg["author_id"]),
                    author_name=d_msg.get("author_name", "unknown"),
                    content=d_msg["content"],
                    has_attachments=d_msg.get("has_attachments", False),
                    reference_message_id=d_msg.get("reference_message_id"),
                    is_reply=d_msg.get("is_reply", False),
                    reactions=json.dumps(d_msg.get("reactions", [])),
                    timestamp=d_msg["timestamp"]
                )
                session.add(new_msg)
        
        # Delete local messages that are no longer in Discord
        is_complete_history = len(discord_messages) < fetch_limit
        
        if discord_messages:
            oldest_fetched_ts = min(d_msg["timestamp"] for d_msg in discord_messages)
            for local_id, local_msg in local_msgs.items():
                if local_id not in discord_ids:
                    if is_complete_history or local_msg.timestamp >= oldest_fetched_ts:
                        await session.delete(local_msg)
        elif is_complete_history:
            # If we fetched 0 messages and it's a complete history, it means the channel is empty.
            for local_msg in local_msgs.values():
                await session.delete(local_msg)
                
        await session.commit()

async def get_recent_messages(channel_id: str, limit: int = 15) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(ChatMessageModel).where(
            ChatMessageModel.channel_id == str(channel_id)
        ).order_by(desc(ChatMessageModel.timestamp)).limit(limit)
        res = await session.execute(stmt)
        messages = res.scalars().all()
        # Return in chronological order
        return [m.to_dict() for m in reversed(messages)]

async def get_recent_active_user_ids(channel_id: str, limit_users: int = 5, lookback_messages: int = 30) -> List[str]:
    async with AsyncSessionLocal() as session:
        stmt = select(ChatMessageModel.author_id).where(
            ChatMessageModel.channel_id == str(channel_id)
        ).order_by(desc(ChatMessageModel.timestamp)).limit(lookback_messages)
        res = await session.execute(stmt)
        user_ids = []
        for uid in res.scalars().all():
            if uid not in user_ids:
                user_ids.append(uid)
            if len(user_ids) >= limit_users:
                break
        return user_ids

async def search_messages_fts(
    query: str,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    author_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 20
) -> List[Dict[str, Any]]:
    db_path = get_db_file_path()
    results = []
    
    # Clean FTS5 query string
    safe_query = '"' + query.replace('"', '""') + '"' if query else ""
    
    sql_parts = ["SELECT id, channel_id, channel_name, author_id, author_name, content, timestamp FROM chat_messages_fts WHERE 1=1"]
    params = []
    
    if safe_query:
        sql_parts.append("AND chat_messages_fts MATCH ?")
        params.append(safe_query)
    if channel_id:
        sql_parts.append("AND channel_id = ?")
        params.append(str(channel_id))
    if channel_name:
        sql_parts.append("AND channel_name LIKE ?")
        params.append(f"%{channel_name}%")
    if author_name:
        sql_parts.append("AND author_name LIKE ?")
        params.append(f"%{author_name}%")
    if start_date:
        sql_parts.append("AND timestamp >= ?")
        params.append(start_date)
    if end_date:
        sql_parts.append("AND timestamp <= ?")
        params.append(end_date)
        
    sql_parts.append("ORDER BY rank LIMIT ?")
    params.append(limit)
    
    query_str = " ".join(sql_parts)
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query_str, params) as cursor:
                async for row in cursor:
                    results.append(dict(row))
    except Exception as e:
        # Fallback to simple SQL LIKE if FTS expression failed
        async with AsyncSessionLocal() as session:
            stmt = select(ChatMessageModel).where(ChatMessageModel.content.ilike(f"%{query}%")).limit(limit)
            res = await session.execute(stmt)
            results = [m.to_dict() for m in res.scalars().all()]
            
    return results

# --- Memories Helpers ---

async def get_server_memories(
    bot_id: Optional[str] = None,
    guild_id: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(ServerMemoryModel)
        if bot_id:
            stmt = stmt.where((ServerMemoryModel.bot_id == str(bot_id)) | (ServerMemoryModel.bot_id == None))
        if guild_id:
            stmt = stmt.where((ServerMemoryModel.guild_id == str(guild_id)) | (ServerMemoryModel.guild_id == None))
        if category:
            stmt = stmt.where(ServerMemoryModel.category == category)
        if search:
            stmt = stmt.where(
                (ServerMemoryModel.key_phrase.ilike(f"%{search}%")) |
                (ServerMemoryModel.fact.ilike(f"%{search}%"))
            )
        stmt = stmt.order_by(desc(ServerMemoryModel.updated_at))
        res = await session.execute(stmt)
        return [m.to_dict() for m in res.scalars().all()]

# --- Discord User Directory ---

async def upsert_discord_user(
    user_id: str,
    username: str,
    global_name: Optional[str] = None,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    is_bot: bool = False
) -> Dict[str, Any]:
    if not user_id:
        return {}
    async with AsyncSessionLocal() as session:
        stmt = select(DiscordUserModel).where(DiscordUserModel.id == str(user_id))
        res = await session.execute(stmt)
        user = res.scalars().first()
        now = get_utc_now()
        if user:
            if username:
                user.username = username
            if global_name:
                user.global_name = global_name
            if display_name:
                user.display_name = display_name
            if avatar_url:
                user.avatar_url = avatar_url
            user.is_bot = is_bot
            user.last_seen = now
            user.updated_at = now
            await session.commit()
            await session.refresh(user)
            return user.to_dict()
        else:
            user = DiscordUserModel(
                id=str(user_id),
                username=username or str(user_id),
                global_name=global_name or username,
                display_name=display_name or global_name or username or str(user_id),
                avatar_url=avatar_url,
                is_bot=is_bot,
                first_seen=now,
                last_seen=now,
                updated_at=now
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.to_dict()

async def get_discord_user(user_id: str) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(DiscordUserModel).where(DiscordUserModel.id == str(user_id))
        res = await session.execute(stmt)
        user = res.scalars().first()
        return user.to_dict() if user else None

async def list_discord_users(search: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(DiscordUserModel)
        if search:
            stmt = stmt.where(
                (DiscordUserModel.username.ilike(f"%{search}%")) |
                (DiscordUserModel.display_name.ilike(f"%{search}%")) |
                (DiscordUserModel.global_name.ilike(f"%{search}%")) |
                (DiscordUserModel.id.ilike(f"%{search}%"))
            )
        stmt = stmt.order_by(desc(DiscordUserModel.last_seen)).limit(limit)
        res = await session.execute(stmt)
        return [u.to_dict() for u in res.scalars().all()]

async def get_user_memories(
    bot_id: Optional[str] = None,
    user_ids: Optional[List[str]] = None,
    username: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(UserMemoryModel)
        if bot_id:
            stmt = stmt.where((UserMemoryModel.bot_id == str(bot_id)) | (UserMemoryModel.bot_id == None))
        if user_ids:
            stmt = stmt.where(UserMemoryModel.user_id.in_([str(u) for u in user_ids]))
        if username:
            stmt = stmt.where(UserMemoryModel.username.ilike(f"%{username}%"))
        if search:
            stmt = stmt.where(
                (UserMemoryModel.fact.ilike(f"%{search}%")) |
                (UserMemoryModel.username.ilike(f"%{search}%")) |
                (UserMemoryModel.user_id.ilike(f"%{search}%"))
            )
        stmt = stmt.order_by(desc(UserMemoryModel.updated_at))
        res = await session.execute(stmt)
        memories = [m.to_dict() for m in res.scalars().all()]
        
        # Enrich with user profile data from discord_users
        if memories:
            uids = list({m["user_id"] for m in memories if m.get("user_id")})
            if uids:
                u_stmt = select(DiscordUserModel).where(DiscordUserModel.id.in_(uids))
                u_res = await session.execute(u_stmt)
                u_map = {u.id: u.to_dict() for u in u_res.scalars().all()}
                for m in memories:
                    prof = u_map.get(m["user_id"])
                    if prof:
                        m["avatar_url"] = prof.get("avatar_url")
                        m["global_name"] = prof.get("global_name")
                        m["display_name"] = prof.get("display_name")
                        m["user_handle"] = prof.get("username")
                    else:
                        m["display_name"] = m["username"]
                        m["user_handle"] = m["username"]
                        m["avatar_url"] = None
        return memories

async def get_user_memories_grouped_by_user(
    bot_id: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Returns memories grouped per user with full profile metadata for user profile card display."""
    memories = await get_user_memories(bot_id=bot_id, search=search)
    
    # Group by user_id
    grouped_map: Dict[str, Dict[str, Any]] = {}
    for m in memories:
        uid = m.get("user_id") or "unknown"
        if uid not in grouped_map:
            grouped_map[uid] = {
                "user_id": uid,
                "username": m.get("user_handle") or m.get("username") or "unknown",
                "display_name": m.get("display_name") or m.get("username") or uid,
                "global_name": m.get("global_name"),
                "avatar_url": m.get("avatar_url"),
                "memories_count": 0,
                "latest_update": m.get("updated_at") or m.get("created_at"),
                "memories": []
            }
        grouped_map[uid]["memories"].append(m)
        grouped_map[uid]["memories_count"] += 1
        
    return list(grouped_map.values())

async def save_or_update_server_memory(
    key_phrase: str,
    fact: str,
    category: str = "general",
    guild_id: Optional[str] = None,
    bot_id: Optional[str] = None
) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        # Check if existing key_phrase exists for this bot
        stmt = select(ServerMemoryModel).where(ServerMemoryModel.key_phrase.ilike(key_phrase))
        if bot_id:
            stmt = stmt.where(ServerMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        existing = res.scalars().first()
        if existing:
            existing.fact = fact
            existing.category = category
            if bot_id:
                existing.bot_id = str(bot_id)
            existing.updated_at = get_utc_now()
            await session.commit()
            await session.refresh(existing)
            return existing.to_dict()
        else:
            new_mem = ServerMemoryModel(
                id=str(uuid.uuid4()),
                bot_id=str(bot_id) if bot_id else None,
                guild_id=str(guild_id) if guild_id else None,
                key_phrase=key_phrase,
                fact=fact,
                category=category,
                created_at=get_utc_now(),
                updated_at=get_utc_now()
            )
            session.add(new_mem)
            await session.commit()
            await session.refresh(new_mem)
            return new_mem.to_dict()

async def update_server_memory_by_id(memory_id: str, new_fact: str, bot_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(ServerMemoryModel).where(ServerMemoryModel.id == str(memory_id))
        if bot_id:
            stmt = stmt.where(ServerMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        existing = res.scalars().first()
        if existing:
            existing.fact = new_fact
            existing.updated_at = get_utc_now()
            await session.commit()
            await session.refresh(existing)
            return existing.to_dict()
        return None

async def remove_server_memory(memory_id_or_keyword: str, bot_id: Optional[str] = None) -> bool:
    async with AsyncSessionLocal() as session:
        stmt = delete(ServerMemoryModel).where(
            (ServerMemoryModel.id == memory_id_or_keyword) |
            (ServerMemoryModel.key_phrase.ilike(memory_id_or_keyword))
        )
        if bot_id:
            stmt = stmt.where(ServerMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0

async def save_or_update_user_memory(
    user_id: str,
    username: str,
    fact: str,
    category: str = "general",
    bot_id: Optional[str] = None
) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        # Check if identical fact or similar exists for this bot & user
        stmt = select(UserMemoryModel).where(
            (UserMemoryModel.user_id == str(user_id)) &
            (UserMemoryModel.fact.ilike(fact))
        )
        if bot_id:
            stmt = stmt.where(UserMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        existing = res.scalars().first()
        if existing:
            existing.category = category
            if username and username != "Unknown":
                existing.username = username
            if bot_id:
                existing.bot_id = str(bot_id)
            existing.updated_at = get_utc_now()
            await session.commit()
            await session.refresh(existing)
            res_dict = existing.to_dict()
        else:
            new_mem = UserMemoryModel(
                id=str(uuid.uuid4()),
                bot_id=str(bot_id) if bot_id else None,
                user_id=str(user_id),
                username=username or "Unknown",
                fact=fact,
                category=category,
                created_at=get_utc_now(),
                updated_at=get_utc_now()
            )
            session.add(new_mem)
            await session.commit()
            await session.refresh(new_mem)
            res_dict = new_mem.to_dict()

    # Sync Discord User Directory in background
    try:
        await upsert_discord_user(
            user_id=str(user_id),
            username=username or str(user_id),
            display_name=username
        )
    except Exception:
        pass

    return res_dict

async def update_user_memory_by_id(memory_id: str, new_fact: str, bot_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        stmt = select(UserMemoryModel).where(UserMemoryModel.id == str(memory_id))
        if bot_id:
            stmt = stmt.where(UserMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        existing = res.scalars().first()
        if existing:
            existing.fact = new_fact
            existing.updated_at = get_utc_now()
            await session.commit()
            await session.refresh(existing)
            return existing.to_dict()
        return None

async def remove_user_memory(
    memory_id_or_fact_key: str,
    user_id: Optional[str] = None,
    bot_id: Optional[str] = None
) -> bool:
    async with AsyncSessionLocal() as session:
        stmt = delete(UserMemoryModel).where(
            (UserMemoryModel.id == memory_id_or_fact_key) |
            (UserMemoryModel.fact.ilike(f"%{memory_id_or_fact_key}%"))
        )
        if user_id:
            stmt = stmt.where(UserMemoryModel.user_id == str(user_id))
        if bot_id:
            stmt = stmt.where(UserMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0

async def wipe_user_memories(user_id: str, bot_id: Optional[str] = None) -> int:
    async with AsyncSessionLocal() as session:
        stmt = delete(UserMemoryModel).where(UserMemoryModel.user_id == str(user_id))
        if bot_id:
            stmt = stmt.where(UserMemoryModel.bot_id == str(bot_id))
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount

# --- Audit & Stats ---

async def log_audit(
    bot_id: Optional[str],
    user_id: Optional[str],
    channel_id: Optional[str],
    model_used: Optional[str],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    tools_called: Optional[List[Dict[str, Any]]] = None,
    refused: bool = False,
    input_text: Optional[str] = None,
    output_text: Optional[str] = None,
    error_message: Optional[str] = None,
    system_prompt: Optional[str] = None
) -> AuditLogModel:
    async with AsyncSessionLocal() as session:
        audit = AuditLogModel(
            id=str(uuid.uuid4()),
            bot_id=str(bot_id) if bot_id else None,
            user_id=str(user_id) if user_id else None,
            channel_id=str(channel_id) if channel_id else None,
            model_used=model_used or "unknown",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tools_called=json.dumps(tools_called or []),
            refused=refused,
            input_text=input_text,
            output_text=output_text,
            error_message=error_message,
            system_prompt=system_prompt,
            timestamp=get_utc_now()
        )
        session.add(audit)
        await session.commit()
        await session.refresh(audit)
        return audit

async def get_dashboard_stats() -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        # Totals
        total_bots = (await session.execute(select(func.count(BotModel.id)))).scalar() or 0
        active_bots = (await session.execute(select(func.count(BotModel.id)).where(BotModel.is_running == True))).scalar() or 0
        total_endpoints = (await session.execute(select(func.count(EndpointModel.id)))).scalar() or 0
        total_server_memories = (await session.execute(select(func.count(ServerMemoryModel.id)))).scalar() or 0
        total_user_memories = (await session.execute(select(func.count(UserMemoryModel.id)))).scalar() or 0
        total_messages = (await session.execute(select(func.count(ChatMessageModel.id)))).scalar() or 0
        
        # Token metrics
        token_stats = (await session.execute(
            select(
                func.sum(AuditLogModel.prompt_tokens),
                func.sum(AuditLogModel.completion_tokens),
                func.sum(AuditLogModel.total_tokens),
                func.count(AuditLogModel.id)
            )
        )).first()
        
        prompt_tokens = token_stats[0] or 0
        completion_tokens = token_stats[1] or 0
        total_tokens = token_stats[2] or 0
        total_ai_requests = token_stats[3] or 0
        
        # Tokens by model
        model_stats_res = await session.execute(
            select(
                AuditLogModel.model_used,
                func.sum(AuditLogModel.total_tokens),
                func.count(AuditLogModel.id)
            ).group_by(AuditLogModel.model_used)
        )
        tokens_by_model = [
            {"model": row[0] or "unknown", "tokens": row[1] or 0, "requests": row[2]}
            for row in model_stats_res.all()
        ]
        
        # Tools called counts
        all_audits = (await session.execute(select(AuditLogModel.tools_called))).scalars().all()
        tool_counts: Dict[str, int] = {}
        for tc in all_audits:
            if tc:
                try:
                    tools = json.loads(tc)
                    for t in tools:
                        name = t.get("name") if isinstance(t, dict) else str(t)
                        tool_counts[name] = tool_counts.get(name, 0) + 1
                except Exception:
                    pass
                    
        # Top users
        top_users_res = await session.execute(
            select(
                ChatMessageModel.author_name,
                func.count(ChatMessageModel.id)
            ).group_by(ChatMessageModel.author_name).order_by(desc(func.count(ChatMessageModel.id))).limit(8)
        )
        top_users = [{"author": row[0], "count": row[1]} for row in top_users_res.all()]
        
        # Recent audit logs
        recent_audits_res = await session.execute(
            select(AuditLogModel).order_by(desc(AuditLogModel.timestamp)).limit(15)
        )
        recent_audits = [a.to_dict() for a in recent_audits_res.scalars().all()]
        
        return {
            "summary": {
                "total_bots": total_bots,
                "active_bots": active_bots,
                "total_endpoints": total_endpoints,
                "total_server_memories": total_server_memories,
                "total_user_memories": total_user_memories,
                "total_memories": total_server_memories + total_user_memories,
                "total_messages": total_messages,
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_ai_requests": total_ai_requests,
            },
            "tokens_by_model": tokens_by_model,
            "tool_counts": tool_counts,
            "top_users": top_users,
            "recent_audits": recent_audits
        }
