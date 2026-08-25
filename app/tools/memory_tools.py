from typing import Dict, Any, Optional
from app.database.queries import (
    save_or_update_user_memory,
    remove_user_memory,
    get_user_memories,
    save_or_update_server_memory,
    remove_server_memory,
    get_server_memories,
    update_server_memory_by_id,
    update_user_memory_by_id,
    upsert_discord_user
)

async def exec_save_user_memory(
    user_id_or_name: str, 
    fact: str, 
    category: str = "general", 
    current_user_id: Optional[str] = None, 
    current_user_name: Optional[str] = None, 
    bot_id: Optional[str] = None,
    discord_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Saves or updates a personal memory about a user for the current bot."""
    clean_id = str(user_id_or_name).strip("<@!> ")
    
    target_id = None
    target_name = clean_id
    
    if clean_id == str(current_user_id) or (current_user_name and clean_id.lower() == str(current_user_name).lower()):
        target_id = current_user_id
        target_name = current_user_name
        if discord_context and "message" in discord_context:
            author = discord_context["message"].author
            avatar_url = str(author.display_avatar.url) if hasattr(author, "display_avatar") and author.display_avatar else None
            global_name = getattr(author, "global_name", None)
            await upsert_discord_user(
                user_id=str(author.id),
                username=author.name,
                global_name=global_name,
                display_name=author.display_name or global_name or author.name,
                avatar_url=avatar_url,
                is_bot=author.bot
            )
    elif clean_id.isdigit():
        target_id = clean_id
        if discord_context and "message" in discord_context:
            guild = discord_context["message"].guild
            if guild:
                member = guild.get_member(int(clean_id))
                if member:
                    target_name = member.display_name or getattr(member, "global_name", None) or member.name
                    avatar_url = str(member.display_avatar.url) if hasattr(member, "display_avatar") and member.display_avatar else None
                    await upsert_discord_user(
                        user_id=str(member.id),
                        username=member.name,
                        global_name=getattr(member, "global_name", None),
                        display_name=target_name,
                        avatar_url=avatar_url,
                        is_bot=member.bot
                    )
    else:
        # Search by username/display name in the server
        if discord_context and "message" in discord_context:
            guild = discord_context["message"].guild
            if guild:
                search_name = clean_id.lower()
                for m in guild.members:
                    if m.name.lower() == search_name or m.display_name.lower() == search_name or (getattr(m, "global_name", None) and m.global_name.lower() == search_name):
                        target_id = str(m.id)
                        target_name = m.display_name or getattr(m, "global_name", None) or m.name
                        avatar_url = str(m.display_avatar.url) if hasattr(m, "display_avatar") and m.display_avatar else None
                        await upsert_discord_user(
                            user_id=str(m.id),
                            username=m.name,
                            global_name=getattr(m, "global_name", None),
                            display_name=target_name,
                            avatar_url=avatar_url,
                            is_bot=m.bot
                        )
                        break
                        
    if not target_id:
        return {
            "status": "error", 
            "message": f"Could not find numeric ID for user '{clean_id}' in the server. Use the 'list_channel_members' tool to check their exact ID or username."
        }
        
    res = await save_or_update_user_memory(
        user_id=target_id,
        username=target_name,
        fact=fact,
        category=category,
        bot_id=bot_id
    )
    return {
        "status": "success", 
        "message": f"User memory saved for '{target_name}' (ID: {target_id}): {fact}", 
        "memory": res
    }

async def exec_remove_user_memory(memory_id_or_fact_key: str, user_id: Optional[str] = None, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Deletes an incorrect or obsolete personal memory for the current bot."""
    success = await remove_user_memory(memory_id_or_fact_key=memory_id_or_fact_key, user_id=user_id, bot_id=bot_id)
    if success:
        return {"status": "success", "message": f"User memory '{memory_id_or_fact_key}' deleted successfully."}
    return {"status": "not_found", "message": f"No user memory found matching '{memory_id_or_fact_key}'."}

async def exec_get_user_profile(user_id_or_name: str, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves all saved memories about a specific user for the current bot."""
    memories = await get_user_memories(bot_id=bot_id, user_ids=[user_id_or_name], username=user_id_or_name, search=None)
    return {"status": "success", "user": user_id_or_name, "memories": memories}

async def exec_save_server_memory(key_phrase: str, fact: str, category: str = "general", guild_id: Optional[str] = None, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Saves a server-wide fact or lore event for the current bot."""
    res = await save_or_update_server_memory(
        key_phrase=key_phrase,
        fact=fact,
        category=category,
        guild_id=guild_id,
        bot_id=bot_id
    )
    return {"status": "success", "message": f"Server memory saved ['{key_phrase}']: {fact}", "memory": res}

async def exec_remove_server_memory(memory_id_or_keyword: str, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Deletes an obsolete or concluded server fact for the current bot."""
    success = await remove_server_memory(memory_id_or_keyword=memory_id_or_keyword, bot_id=bot_id)
    if success:
        return {"status": "success", "message": f"Server memory '{memory_id_or_keyword}' removed successfully."}
    return {"status": "not_found", "message": f"No server memory found matching '{memory_id_or_keyword}'."}

async def exec_get_server_memories(category_or_search: Optional[str] = None, guild_id: Optional[str] = None, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves server facts, events, or global lore for the current bot."""
    memories = await get_server_memories(
        bot_id=bot_id,
        guild_id=guild_id,
        category=category_or_search,
        search=category_or_search
    )
    return {"status": "success", "count": len(memories), "memories": memories}

async def exec_update_server_memory(memory_id: str, new_fact: str, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Updates an existing global server memory using its ID."""
    res = await update_server_memory_by_id(memory_id=memory_id, new_fact=new_fact, bot_id=bot_id)
    if res:
        return {"status": "success", "message": "Server memory updated successfully.", "memory": res}
    return {"status": "not_found", "message": f"No server memory found with ID '{memory_id}'."}

async def exec_update_user_memory(memory_id: str, new_fact: str, bot_id: Optional[str] = None) -> Dict[str, Any]:
    """Updates an existing personal user memory using its ID."""
    res = await update_user_memory_by_id(memory_id=memory_id, new_fact=new_fact, bot_id=bot_id)
    if res:
        return {"status": "success", "message": "User memory updated successfully.", "memory": res}
    return {"status": "not_found", "message": f"No user memory found with ID '{memory_id}'."}
