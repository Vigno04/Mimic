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
