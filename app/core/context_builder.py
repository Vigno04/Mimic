import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional, Tuple
from app.core.config import settings
from app.database.queries import (
    get_server_memories,
    get_user_memories,
    get_recent_messages,
    get_recent_active_user_ids,
    get_bot_last_messages_per_channel,
    get_channels_activity_summary
)

def get_configured_timezone() -> datetime.tzinfo:
    """Returns the configured ZoneInfo timezone or UTC fallback."""
    tz_name = (getattr(settings, "TIMEZONE", None) or "UTC").strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return datetime.timezone.utc

def format_timestamp_for_context(ts_val: Any, target_tz: datetime.tzinfo) -> str:
    """Formats a message timestamp (stored in UTC) into the target timezone for LLM prompt context."""
    if not ts_val:
        return ""
    dt: Optional[datetime.datetime] = None
    if isinstance(ts_val, str):
        try:
            cleaned = ts_val.replace('Z', '').split('+')[0]
            dt = datetime.datetime.fromisoformat(cleaned)
        except Exception:
            return str(ts_val)[:19].replace('T', ' ')
    elif isinstance(ts_val, datetime.datetime):
        dt = ts_val
    else:
        return str(ts_val)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    try:
        local_dt = dt.astimezone(target_tz)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

class ContextBuildResult:
    def __init__(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        injected_server_memories: List[Dict[str, Any]],
        injected_user_memories: List[Dict[str, Any]],
        recent_history: List[Dict[str, Any]]
    ):
        self.messages = messages
        self.system_prompt = system_prompt
        self.injected_server_memories = injected_server_memories
        self.injected_user_memories = injected_user_memories
        self.recent_history = recent_history

    def to_dict(self):
        return {
            "system_prompt": self.system_prompt,
            "injected_server_memories": self.injected_server_memories,
            "injected_user_memories": self.injected_user_memories,
            "messages_count": len(self.messages),
            "recent_history_count": len(self.recent_history)
        }

async def build_llm_context(
    bot_id: str,
    system_prompt: str,
    channel_id: str,
    channel_name: str,
    author_id: str,
    author_name: str,
    current_message: str,
    reply_policy: str = "ai_choice",
    memory_mode: str = "recent_active",
    active_users_count: int = 5,
    recent_messages_count: int = 15,
    attachments: Optional[List[str]] = None,
    guild_id: Optional[str] = None,
    message_id: Optional[str] = None,
    bot_user_id: Optional[str] = None
) -> ContextBuildResult:
    """Builds the message payload and system prompt enriched with memories and lore."""
    tz = get_configured_timezone()
    now_local = datetime.datetime.now(tz)
    tz_label = tz.key if hasattr(tz, "key") else getattr(settings, "TIMEZONE", "UTC")
    now_str = now_local.strftime(f"%Y-%m-%d %H:%M:%S %Z ({tz_label})")
    
    # 1. Retrieve Server Lore and Memories for this Bot
    server_memories = await get_server_memories(bot_id=bot_id, guild_id=guild_id)
    
    # 2. Retrieve User Memories for this Bot
    user_memories: List[Dict[str, Any]] = []
    if memory_mode == "full":
        user_memories = await get_user_memories(bot_id=bot_id)
    elif memory_mode == "recent_active":
        active_uids = await get_recent_active_user_ids(
            channel_id=channel_id,
            limit_users=active_users_count
        )
        if str(author_id) not in active_uids:
            active_uids.append(str(author_id))
        user_memories = await get_user_memories(bot_id=bot_id, user_ids=active_uids)
    elif memory_mode == "tool_only":
        user_memories = []
        
    # 3. Build System Prompt
    system_sections = [
        f"# BOT PERSONA & CORE IDENTITY\n{system_prompt.strip()}\nAlways embody this personality, tone of voice, character traits, quirks, and mannerisms in every message.",
        f"# CONTEXT INFORMATION\n- Current Date and Time: {now_str}\n- Current Channel: #{channel_name or 'general'} (ID: {channel_id})\n- Message Author: {author_name} (ID: {author_id})"
    ]
    
    if server_memories:
        mem_lines = [f"- [ID: {m.get('id')}] [{m.get('key_phrase')}]: {m.get('fact')} (Cat: {m.get('category', 'general')})" for m in server_memories]
        system_sections.append("# SERVER LORE AND MEMORIES (GLOBAL FACTS)\n" + "\n".join(mem_lines))
        
    if user_memories:
        user_mem_lines = []
        for m in user_memories:
            uid = str(m.get('user_id', ''))
            tag = f"<@{uid}>" if uid.isdigit() else uid
            user_mem_lines.append(f"- [ID: {m.get('id')}] {tag} (@{m.get('username')}): {m.get('fact')} [Cat: {m.get('category', 'general')}]")
        system_sections.append("# MEMORIES ABOUT ACTIVE USERS\n" + "\n".join(user_mem_lines))
        
    # Tools and Action Policy (Inspired by DeepSeek Harness Agent Design)
    system_sections.append(
        "# TOOLS & ACTION DECISION POLICY\n"
        "You have access to real-time tools. Follow these strict operational rules:\n"
        "1. REAL-TIME & WEB SEARCH (`web_search`):\n"
        "   - Use `web_search` whenever asked about current events, latest news, real-time facts, release dates, weather, or topics beyond your knowledge cutoff.\n"
        "   - NEVER guess or fabricate information if `web_search` can verify it.\n\n"
        "2. CHAT HISTORY RECALL (`search_chat_history`):\n"
        "   - Use `search_chat_history` when a user asks 'what did we talk about earlier?', 'who said X?', 'did anyone mention Y?', or references previous discussions outside the immediate chat window.\n\n"
        "3. MEMORY DEDUPLICATION & MANAGEMENT (`save_user_memory`, `update_user_memory`, `get_user_profile`, `save_server_memory`, `update_server_memory`):\n"
        "   - PRE-CHECK FIRST: Before saving a memory about a user or server, ALWAYS check if that information is already recorded.\n"
        "     * Check the '# MEMORIES ABOUT ACTIVE USERS' or '# SERVER LORE AND MEMORIES' sections above.\n"
        "     * If the user's full memories are not present in your recent context, call `get_user_profile(user_id_or_name)` first before creating a new entry.\n"
        "   - NO DUPLICATES: If an existing memory already covers this topic or needs updating, DO NOT create a new duplicate! Call `update_user_memory(memory_id, new_fact)` or `update_server_memory(memory_id, new_fact)` using the existing memory ID.\n"
        "   - SILENT & NATURAL: Save or update memories autonomously in the background when users state personal facts (preferences, hobbies, names, timezones) without saying 'I have stored this in my database'.\n\n"
        "4. DISCORD ACTIONS:\n"
        "   - `add_reaction`: Add emoji reactions to express emotion or acknowledge messages naturally.\n"
        "   - `list_channel_members`: Find member IDs or usernames when you need to tag someone who hasn't spoken recently.\n"
        "   - `send_bot_command`: Send prefix commands to interact with other Discord bots (e.g. `!play <song>`, `?help`)."
    )
        
    # Reply Policy & Intelligent Silence [REFUSE]
    if reply_policy == "ai_choice":
        system_sections.append(
            "# REPLY AND SILENCE RULE (ai_choice)\n"
            "You are in 'ai_choice' mode. You can freely choose whether to participate in the conversation based on your personality and context. "
            "If you decide that your intervention is not needed or the chat is not directed at you, "
            "you MUST reply EXCLUSIVELY with the exact word '[REFUSE]' without adding any other text. "
            "This allows you to remain completely silent without disturbing the chat."
        )
    elif reply_policy == "mandatory":
        system_sections.append(
            "# REPLY RULE (mandatory)\n"
            "Always reply to the user's message."
        )
        
    # Formatting Standards
    system_sections.append(
        "# DISCORD FORMATTING STANDARDS\n"
        "- USER TAGS: To tag or mention a user, use the Discord format `<@NUMERIC_ID>` (e.g. `<@123456789>`).\n"
        "  * You can find a user's NUMERIC_ID next to their name in the chat history (format: `[Username - NUMERIC_ID]`).\n"
        "  * If you need to tag someone who hasn't spoken recently, use the `list_channel_members` tool to find their ID.\n"
        "  * NEVER write `<@Username>`.\n"
        "- MESSAGE REFERENCE: Use `msg:MESSAGE_ID` to reference a specific message."
    )
    
    full_system_prompt = "\n\n".join(system_sections)
    
    # 4. Retrieve Recent Message History
    recent_history = await get_recent_messages(channel_id=channel_id, limit=recent_messages_count)
    
    # 5. Build LLM Messages Sequence
    llm_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": full_system_prompt}
    ]
    
    # Add history excluding the last message if it is exactly the same as current_message
    for m in recent_history:
        # Avoid duplicating the last message just sent because we format it with vision below
        if message_id and m.get("id") == str(message_id):
            continue
        elif not message_id and m.get("content") == current_message and m.get("author_id") == str(author_id):
            continue
        is_bot_msg = (
            (m.get("author_id") == str(bot_id)) or
            (bot_user_id and m.get("author_id") == str(bot_user_id))
        )
        role = "assistant" if is_bot_msg else "user"
        formatted_ts = format_timestamp_for_context(m.get('timestamp'), tz)
        ts_str = f"[{formatted_ts}] " if formatted_ts else ""
        llm_messages.append({
            "role": role,
            "content": f"{ts_str}[{m.get('author_name', 'User')} - {m.get('author_id')}] (msg:{m.get('id', 'unknown')}): {m.get('content', '')}"
        })
        
    # Build current message (with Vision if attachments are present)
    current_content: Any = current_message
    if attachments:
        content_parts: List[Dict[str, Any]] = [
            {"type": "text", "text": f"[{now_str}] [{author_name} - {author_id}] (msg:{message_id or 'unknown'}): {current_message or '(sent a visual attachment)'}"}
        ]
        for url in attachments:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        current_content = content_parts
    else:
        current_content = f"[{now_str}] [{author_name} - {author_id}] (msg:{message_id or 'unknown'}): {current_message}"
        
    llm_messages.append({
        "role": "user",
        "content": current_content
    })
    
    return ContextBuildResult(
        messages=llm_messages,
        system_prompt=full_system_prompt,
        injected_server_memories=server_memories,
        injected_user_memories=user_memories,
        recent_history=recent_history
    )

async def build_proactive_context(
    bot_id: str,
    system_prompt: str,
    channel_id: str,
    channel_name: str,
    topic_guidance: Optional[str] = None,
    guild_id: Optional[str] = None,
    bot_user_id: Optional[str] = None,
    bot_name: Optional[str] = None,
    enabled_channels: Optional[List[str]] = None,
    recent_messages_count: int = 15
) -> ContextBuildResult:
    """Builds a specialized context for spontaneous / proactive AI activation."""
    tz = get_configured_timezone()
    now_local = datetime.datetime.now(tz)
    tz_label = tz.key if hasattr(tz, "key") else getattr(settings, "TIMEZONE", "UTC")
    day_name = now_local.strftime("%A")
    now_str = now_local.strftime(f"%Y-%m-%d %H:%M:%S %Z ({tz_label}) - {day_name}")
    
    # 1. Server Lore & Active User Memories
    server_memories = await get_server_memories(bot_id=bot_id, guild_id=guild_id)
    user_memories = await get_user_memories(bot_id=bot_id)
    
    # 2. Activity Overview & Bot's last messages across channels
    bot_last_msgs = await get_bot_last_messages_per_channel(bot_author_id=bot_user_id, bot_id=bot_id)
    channels_summary = await get_channels_activity_summary(channel_ids=enabled_channels)
    
    # 3. Build System Prompt Sections
    system_sections = [
        f"# BOT PERSONA & CORE IDENTITY\n{system_prompt.strip()}\nAlways embody this personality, tone of voice, character traits, quirks, and mannerisms in every message.",
        f"# CONTEXT INFORMATION\n- Current Date and Time: {now_str}\n- Primary Channel Context: #{channel_name or 'general'} (ID: {channel_id})\n- Your Bot Handle: @{bot_name or 'Assistant'} (ID: {bot_user_id or bot_id})"
    ]
    
    # Cross-Channel & Bot History section
    overview_lines = []
    if bot_last_msgs:
        overview_lines.append("### Your Last Messages Across Channels:")
        for bm in bot_last_msgs[:6]:
            overview_lines.append(f"- #{bm.get('channel_name')} (ID: {bm.get('channel_id')}): {bm.get('elapsed')} (msg: \"{bm.get('content', '')[:80]}\")")
    else:
        overview_lines.append("### Your Last Messages Across Channels:\n- You have not posted any recent messages yet.")
        
    if channels_summary:
        overview_lines.append("\n### Channel Activity Overview:")
        for cs in channels_summary[:8]:
            overview_lines.append(f"- #{cs.get('channel_name')} (ID: {cs.get('channel_id')}): Latest activity {cs.get('elapsed')} by @{cs.get('latest_author')}")
            
    system_sections.append("# SERVER & CROSS-CHANNEL OVERVIEW\n" + "\n".join(overview_lines))
    
    if server_memories:
        mem_lines = [f"- [ID: {m.get('id')}] [{m.get('key_phrase')}]: {m.get('fact')} (Cat: {m.get('category', 'general')})" for m in server_memories]
        system_sections.append("# SERVER LORE AND MEMORIES (GLOBAL FACTS)\n" + "\n".join(mem_lines))
        
    if user_memories:
        user_mem_lines = []
        for m in user_memories[:12]:
            uid = str(m.get('user_id', ''))
            tag = f"<@{uid}>" if uid.isdigit() else uid
            user_mem_lines.append(f"- {tag} (@{m.get('username')}): {m.get('fact')}")
        system_sections.append("# USER FACTS & SITUATIONS TO REMEMBER\n" + "\n".join(user_mem_lines))
        
    # Tool policy
    system_sections.append(
        "# TOOLS FOR INVESTIGATION & ACTIONS\n"
        "You have access to tools to check other channels or search before deciding:\n"
        "1. `get_channel_history(channel_id_or_name)`: Read recent messages from any other channel in the server to see what users are talking about.\n"
        "2. `search_chat_history(query)`: Search past discussions across the server.\n"
        "3. `send_message_to_channel(message_text, channel_id)`: Send a message directly to another channel if you find it more appropriate than the primary channel.\n"
        "4. `web_search(query)`: Search online for real-time news or events."
    )
    
    # Proactive Decision Directive
    guidance_line = f"\n- OPERATIONAL GOAL / GUIDANCE: {topic_guidance.strip()}" if topic_guidance else ""
    system_sections.append(
        f"# PROACTIVE INITIATION & SILENCE DIRECTIVE (ai_choice){guidance_line}\n"
        "You are evaluating an autonomous spontaneous interaction opportunity.\n"
        "- PURPOSE: You may check in on a situation mentioned earlier, ask a user how a project/event went, start a relevant topic based on server lore or day of the week, or share a thoughtful insight.\n"
        "- MULTI-CHANNEL FREEDOM: You are evaluating #{channel_name}, but you can inspect other channels with `get_channel_history` and write to them using `send_message_to_channel` if a topic there is more timely.\n"
        "- CRITICAL DECISION RULE: If there is no genuine, natural, or timely reason to speak right now, or if it would feel forced/awkward, you MUST reply EXCLUSIVELY with the exact word '[REFUSE]'.\n"
        "- If you choose to speak in #{channel_name}, output only your Discord message naturally in character without any extra meta-commentary."
    )
    
    full_system_prompt = "\n\n".join(system_sections)
    
    # Recent history of the primary channel
    recent_history = await get_recent_messages(channel_id=channel_id, limit=recent_messages_count)
    
    llm_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": full_system_prompt}
    ]
    
    for m in recent_history:
        is_bot_msg = (
            (m.get("author_id") == str(bot_id)) or
            (bot_user_id and m.get("author_id") == str(bot_user_id))
        )
        role = "assistant" if is_bot_msg else "user"
        formatted_ts = format_timestamp_for_context(m.get('timestamp'), tz)
        ts_str = f"[{formatted_ts}] " if formatted_ts else ""
        llm_messages.append({
            "role": role,
            "content": f"{ts_str}[{m.get('author_name', 'User')} - {m.get('author_id')}] (msg:{m.get('id', 'unknown')}): {m.get('content', '')}"
        })
        
    # Trigger prompt
    llm_messages.append({
        "role": "user",
        "content": f"[SYSTEM EVENT: Autonomous Proactive Opportunity at {now_str} in #{channel_name}] Evaluate current context, recent activity, and user facts. If you wish to post in #{channel_name}, provide your message. If another channel is better, use `send_message_to_channel`. If no interaction is needed now, reply '[REFUSE]'."
    })
    
    return ContextBuildResult(
        messages=llm_messages,
        system_prompt=full_system_prompt,
        injected_server_memories=server_memories,
        injected_user_memories=user_memories,
        recent_history=recent_history
    )

