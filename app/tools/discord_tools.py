import re
from typing import Dict, Any, Optional, Tuple, List
import discord

def _normalize_text(text: str) -> str:
    """Removes symbols, hyphens, underscores, spaces, and emojis to facilitate name comparison."""
    return re.sub(r'[^a-zA-Z0-9\u00C0-\u017F]', '', text.lower())

def _resolve_channel(guild: Optional[discord.Guild], channel_identifier: Optional[str]) -> Tuple[Optional[Any], List[str], List[str]]:
    """
    Resolves a channel strictly by exact numeric ID or exact channel name.
    If not found, it does NOT auto-fix or auto-execute. Instead, it returns suggestions and available channels:
    - matched_channel: Optional[discord.TextChannel]
    - suggestions: List[str] (e.g. ['#general (ID: 123456)'])
    - available: List[str] (all available text channels with IDs)
    """
    if not guild:
        return None, [], []
        
    text_channels = list(guild.text_channels) + list(guild.threads)
    available = [f"#{c.name} (ID: {c.id})" for c in guild.text_channels]
    
    if not channel_identifier:
        return None, [], available
        
    clean_id = str(channel_identifier).strip("<#> ").lower()
    
    # 1. Exact search by numeric ID
    if clean_id.isdigit():
        ch = guild.get_channel(int(clean_id))
        if ch:
            return ch, [], available
            
    # 2. Exact search by channel name (case insensitive)
    for c in text_channels:
        if c.name.lower() == clean_id:
            return c, [], available
            
    # 3. If no exact match, find suggestions without auto-fixing
    norm_target = _normalize_text(clean_id)
    suggestions: List[str] = []
    
    if norm_target:
        for c in text_channels:
            norm_c = _normalize_text(c.name)
            if norm_target == norm_c or (len(norm_target) >= 3 and (norm_target in norm_c or norm_c in norm_target)):
                suggestions.append(f"#{c.name} (ID: {c.id})")
                
    return None, suggestions, available

async def exec_add_reaction(
    emoji: str,
    message_id: Optional[str] = None,
    discord_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Adds an emoji reaction to the current or specified message on Discord."""
    if discord_context and "message" in discord_context:
        msg = discord_context["message"]
        channel = discord_context.get("channel")
        
        # If a specific message_id is provided
        if message_id and str(message_id) != str(msg.id) and channel:
            try:
                clean_mid = str(message_id).strip("msg: ")
                if clean_mid.isdigit():
                    target_msg = await channel.fetch_message(int(clean_mid))
                    if target_msg:
                        msg = target_msg
            except Exception:
                pass
                
        try:
            await msg.add_reaction(emoji)
            return {"status": "success", "message": f"Reaction '{emoji}' added to message {msg.id}."}
        except Exception as e:
            return {"status": "error", "message": f"Could not add reaction '{emoji}': {str(e)}"}
            
    return {
        "status": "simulated",
        "action": "add_reaction",
        "emoji": emoji,
        "message_id": message_id,
        "message": f"Reaction '{emoji}' simulated successfully on message {message_id or 'current'}."
    }

async def exec_send_bot_command(
    command_string: str,
    channel_id: Optional[str] = None,
    discord_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Sends a prefix command for other bots (!play, -skip, etc.) in a Discord channel."""
    if discord_context and "message" in discord_context:
        guild = discord_context["message"].guild
        channel = discord_context["channel"]
        
        if guild and channel_id:
            found_channel, suggestions, available = _resolve_channel(guild, channel_id)
            if found_channel:
                channel = found_channel
            else:
                if suggestions:
                    sug_str = ", ".join(suggestions)
                    return {
                        "status": "error",
                        "message": f"Channel '{channel_id}' not found in the server. Did you mean: {sug_str}? If you do not know the exact channel name or ID, use the 'get_server_info' tool to view available channels or specify the exact numeric ID."
                    }
                else:
                    av_str = ", ".join(available) if available else "None"
                    return {
                        "status": "error",
                        "message": f"Channel '{channel_id}' not found in the server. Available channels: {av_str}. Use the 'get_server_info' tool to discover channels or provide the exact channel numeric ID."
                    }
                
        try:
            await channel.send(command_string)
            return {"status": "success", "message": f"Command '{command_string}' sent successfully to channel #{channel.name} (ID: {channel.id})."}
        except Exception as e:
            return {"status": "error", "message": f"Error sending command to channel #{getattr(channel, 'name', channel_id)}: {str(e)}"}
            
    return {
        "status": "simulated",
        "action": "send_bot_command",
        "command_string": command_string,
        "channel_id": channel_id,
        "message": f"Command '{command_string}' simulated on channel {channel_id or 'current'}."
    }

async def exec_send_message_to_channel(
    message_text: str,
    channel_id: str,
    discord_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Sends a text message to a specific Discord channel."""
    if discord_context and "message" in discord_context:
        guild = discord_context["message"].guild
        if guild:
            channel, suggestions, available = _resolve_channel(guild, channel_id)
            
            if channel:
                try:
                    sent_msg = await channel.send(message_text)
                    return {
                        "status": "success",
                        "message": f"Message sent successfully to channel #{channel.name} (ID: {channel.id}, MsgID: {sent_msg.id}).",
                        "channel_name": channel.name,
                        "channel_id": str(channel.id)
                    }
                except Exception as e:
                    return {"status": "error", "message": f"Error sending message to channel #{channel.name}: {str(e)}"}
            
            if suggestions:
                sug_str = ", ".join(suggestions)
                return {
                    "status": "error",
                    "message": f"Channel '{channel_id}' not found in the server. Did you mean: {sug_str}? If you do not know the exact channel name or ID, use the 'get_server_info' tool to list available channels or use the exact channel numeric ID."
                }
            else:
                av_str = ", ".join(available) if available else "None"
                return {
                    "status": "error",
                    "message": f"Channel '{channel_id}' not found in the server. Available text channels: {av_str}. Use the 'get_server_info' tool to view available channels or provide the exact channel numeric ID."
                }
            
    return {
        "status": "simulated",
        "action": "send_message_to_channel",
        "message_text": message_text,
        "channel_id": channel_id,
        "message": f"Message send '{message_text}' simulated on channel {channel_id}."
    }

async def exec_get_server_info(discord_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Retrieves server statistics, details, and the full list of text channels."""
    if discord_context and "message" in discord_context:
        guild = discord_context["message"].guild
        if guild:
            channels_list = [
                {
                    "name": c.name,
                    "id": str(c.id),
                    "category": c.category.name if c.category else None,
                    "topic": getattr(c, "topic", None)
                }
                for c in guild.text_channels
            ]
            return {
                "status": "success",
                "server_name": guild.name,
                "server_id": str(guild.id),
                "member_count": guild.member_count,
                "role_count": len(guild.roles),
                "channel_count": len(guild.channels),
                "text_channels": channels_list,
                "owner": guild.owner.display_name if guild.owner else "Unknown"
            }
    return {"status": "error", "message": "Server context not available."}

async def exec_get_channel_info(channel_id: Optional[str] = None, discord_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Retrieves information about a specific channel (or current channel if omitted)."""
    if discord_context and "message" in discord_context:
        guild = discord_context["message"].guild
        channel = discord_context["channel"]
        
        if guild and channel_id:
            found, suggestions, available = _resolve_channel(guild, channel_id)
            if found:
                channel = found
            else:
                sug_str = f" Did you mean: {', '.join(suggestions)}." if suggestions else ""
                return {
                    "status": "error",
                    "message": f"Channel '{channel_id}' not found in the server.{sug_str} Available channels: {', '.join(available) if available else 'None'}."
                }
            
        if channel:
            return {
                "status": "success",
                "channel_name": getattr(channel, "name", "Unknown"),
                "channel_id": str(getattr(channel, "id", "")),
                "channel_topic": getattr(channel, "topic", "No topic set"),
                "is_nsfw": getattr(channel, "is_nsfw", lambda: False)(),
                "category": channel.category.name if getattr(channel, "category", None) else "None"
            }
    return {"status": "error", "message": "Channel context not available."}

async def exec_get_user_info(user_id: str, discord_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Retrieves detailed information about a specific user in the server."""
    if discord_context and "message" in discord_context:
        guild = discord_context["message"].guild
        if guild:
            clean_id = user_id.strip("<@!> ")
            member = None
            
            # Exact search by numeric ID
            if clean_id.isdigit():
                member = guild.get_member(int(clean_id))
            
            # Fallback search by username or display name
            if not member:
                for m in guild.members:
                    if m.name.lower() == clean_id.lower() or m.display_name.lower() == clean_id.lower():
                        member = m
                        break
                        
            if member:
                roles = [role.name for role in member.roles if role.name != "@everyone"]
                return {
                    "status": "success",
                    "user_id": str(member.id),
                    "username": member.name,
                    "display_name": member.display_name,
                    "is_bot": member.bot,
                    "joined_at": member.joined_at.strftime("%Y-%m-%d %H:%M:%S") if member.joined_at else "Unknown",
                    "roles": roles[:10]
                }
            return {
                "status": "error",
                "message": f"User '{clean_id}' not found in the server. If you do not know the numeric ID, use the 'list_channel_members' tool to find channel members and their IDs."
            }
            
    return {"status": "error", "message": "Server context not available to search for user."}

async def exec_list_channel_members(channel_id: Optional[str] = None, limit: int = 50, discord_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Lists members who have access to a channel, or server members if the channel is public."""
    if discord_context and "message" in discord_context:
        guild = discord_context["message"].guild
        channel = discord_context["channel"]
        
        if guild and channel_id:
            found, suggestions, available = _resolve_channel(guild, channel_id)
            if found:
                channel = found
            else:
                sug_str = f" Did you mean: {', '.join(suggestions)}." if suggestions else ""
                return {
                    "status": "error",
                    "message": f"Channel '{channel_id}' not found.{sug_str} Available channels: {', '.join(available) if available else 'None'}."
                }
                
        if channel and hasattr(channel, "members"):
            members = channel.members
            active_members = [m for m in members if not m.bot]
            member_names = [f"{m.display_name} ({m.name}) - ID: {m.id}" for m in active_members[:limit]]
            has_more = len(active_members) > limit
            
            return {
                "status": "success",
                "channel": channel.name,
                "channel_id": str(channel.id),
                "total_members": len(active_members),
                "listed_members": len(member_names),
                "members": member_names,
                "has_more": has_more
            }
        elif guild:
            active_members = [m for m in guild.members if not m.bot]
            member_names = [f"{m.display_name} ({m.name}) - ID: {m.id}" for m in active_members[:limit]]
            has_more = len(active_members) > limit
            return {
                "status": "success",
                "server": guild.name,
                "server_id": str(guild.id),
                "total_members": len(active_members),
                "listed_members": len(member_names),
                "members": member_names,
                "has_more": has_more
            }
            
    return {"status": "error", "message": "Could not retrieve channel members from current context."}

async def exec_get_channel_history(
    channel_id_or_name: str,
    limit: int = 15,
    discord_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Retrieves recent chat messages from a specific channel to inspect ongoing discussions."""
    limit = min(max(1, limit), 30)
    
    if discord_context:
        guild = discord_context.get("guild") or (discord_context.get("message").guild if discord_context.get("message") else None)
        target_channel = None
        if guild:
            target_channel, suggestions, available = _resolve_channel(guild, channel_id_or_name)
            if not target_channel:
                sug_str = f" Did you mean: {', '.join(suggestions)}." if suggestions else ""
                return {
                    "status": "error",
                    "message": f"Channel '{channel_id_or_name}' not found.{sug_str} Available channels: {', '.join(available) if available else 'None'}."
                }
        elif discord_context.get("channel"):
            target_channel = discord_context.get("channel")
            
        if target_channel and hasattr(target_channel, "history"):
            try:
                msgs = []
                async for msg in target_channel.history(limit=limit):
                    ts_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                    msgs.append({
                        "id": str(msg.id),
                        "author": msg.author.display_name or msg.author.name,
                        "author_id": str(msg.author.id),
                        "is_bot": msg.author.bot,
                        "content": msg.content,
                        "timestamp": ts_str,
                        "attachments": len(msg.attachments) > 0
                    })
                return {
                    "status": "success",
                    "channel_name": getattr(target_channel, "name", "channel"),
                    "channel_id": str(target_channel.id),
                    "count": len(msgs),
                    "messages": list(reversed(msgs))  # Chronological order
                }
            except Exception:
                pass

    # Fallback to local DB queries
    from app.database.queries import get_recent_messages
    clean_id = str(channel_id_or_name).strip("<#> ")
    db_msgs = await get_recent_messages(channel_id=clean_id, limit=limit)
    return {
        "status": "success",
        "channel_id": clean_id,
        "count": len(db_msgs),
        "messages": [
            {
                "id": m.get("id"),
                "author": m.get("author_name"),
                "author_id": m.get("author_id"),
                "content": m.get("content"),
                "timestamp": str(m.get("timestamp", ""))[:19].replace('T', ' ')
            }
            for m in db_msgs
        ]
    }


async def exec_fetch_message_media(
    message_id: str,
    channel_id: Optional[str] = None,
    discord_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Fetches fresh image/media data from a Discord message by its ID.
    Returns base64 data URIs for all visual attachments and stickers found.
    The data URIs are embedded in a special '__vision_urls__' key so the
    tool dispatcher can inject them as image content into the LLM context.
    """
    import base64
    import httpx

    if not discord_context:
        return {"status": "error", "message": "Discord context not available."}

    guild = discord_context.get("guild") or (
        discord_context.get("message").guild if discord_context.get("message") else None
    )
    channel = discord_context.get("channel")

    # Resolve target channel
    if channel_id and guild:
        found, _, _ = _resolve_channel(guild, channel_id)
        if found:
            channel = found

    if not channel:
        return {"status": "error", "message": "Could not resolve channel to fetch message from."}

    # Clean message_id (strip 'msg:' prefix if present)
    clean_msg_id = str(message_id).strip().lstrip("msg:").strip()
    if not clean_msg_id.isdigit():
        return {"status": "error", "message": f"Invalid message ID: '{message_id}'. Must be a numeric Discord message ID."}

    try:
        discord_msg = await channel.fetch_message(int(clean_msg_id))
    except Exception as e:
        return {"status": "error", "message": f"Could not fetch message {clean_msg_id}: {str(e)}"}

    vision_urls = []   # data URIs for LLM vision
    media_descriptions = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:

        # 1. Regular image attachments
        for att in discord_msg.attachments:
            is_visual = (
                (att.content_type and att.content_type.startswith("image/")) or
                any(att.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"])
            )
            if is_visual:
                try:
                    r = await http.get(att.url)
                    if r.status_code == 200:
                        ext = att.filename.lower().rsplit(".", 1)[-1] if "." in att.filename else "png"
                        ct = att.content_type or ""
                        if "gif" in ct or ext == "gif":
                            mime = "image/gif"
                        elif "jpeg" in ct or ext in ("jpg", "jpeg"):
                            mime = "image/jpeg"
                        elif "webp" in ct or ext == "webp":
                            mime = "image/webp"
                        else:
                            mime = "image/png"
                        b64 = base64.b64encode(r.content).decode("utf-8")
                        vision_urls.append(f"data:{mime};base64,{b64}")
                        media_descriptions.append(f"Attachment: {att.filename} ({mime})")
                    else:
                        media_descriptions.append(f"Attachment {att.filename}: HTTP {r.status_code} - URL may have expired.")
                except Exception as e:
                    media_descriptions.append(f"Attachment {att.filename}: download error - {str(e)}")

        # 2. Stickers (Discord CDN URLs for stickers are permanent)
        for sticker in discord_msg.stickers:
            cdn_gif = f"https://media.discordapp.net/stickers/{sticker.id}.gif"
            cdn_png = f"https://media.discordapp.net/stickers/{sticker.id}.png"
            downloaded = False
            for cdn_url, mime in [(cdn_gif, "image/gif"), (cdn_png, "image/png")]:
                try:
                    r = await http.get(cdn_url)
                    if r.status_code == 200:
                        b64 = base64.b64encode(r.content).decode("utf-8")
                        vision_urls.append(f"data:{mime};base64,{b64}")
                        media_descriptions.append(f"Sticker: {sticker.name} ({mime})")
                        downloaded = True
                        break
                except Exception:
                    pass
            if not downloaded:
                media_descriptions.append(f"Sticker {sticker.name}: could not download from CDN.")

        # 3. Embeds with images
        for embed in discord_msg.embeds:
            embed_url = None
            if embed.image and embed.image.url:
                embed_url = embed.image.url
            elif embed.thumbnail and embed.thumbnail.url:
                embed_url = embed.thumbnail.url
            if embed_url:
                try:
                    r = await http.get(embed_url)
                    if r.status_code == 200:
                        ct = r.headers.get("content-type", "image/png")
                        mime = ct.split(";")[0].strip() if "image/" in ct else "image/png"
                        b64 = base64.b64encode(r.content).decode("utf-8")
                        vision_urls.append(f"data:{mime};base64,{b64}")
                        media_descriptions.append(f"Embed image from message {clean_msg_id} ({mime})")
                except Exception as e:
                    media_descriptions.append(f"Embed image: download error - {str(e)}")

    if not vision_urls and not media_descriptions:
        return {
            "status": "no_media",
            "message": f"Message {clean_msg_id} has no visual attachments (images, stickers, or embedded images).",
            "message_content": discord_msg.content or "(no text)"
        }

    return {
        "status": "success",
        "message_id": clean_msg_id,
        "message_content": discord_msg.content or "(no text)",
        "media_count": len(vision_urls),
        "media_descriptions": media_descriptions,
        # Special key: the tool dispatcher will inject these as image_url parts
        "__vision_urls__": vision_urls
    }
