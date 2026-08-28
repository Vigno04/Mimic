import asyncio
import time
import logging
import base64
from typing import Dict, Any, List, Optional
import discord
from discord.ext import commands

from app.database.queries import (
    log_chat_message, 
    log_audit, 
    update_chat_message, 
    delete_chat_message_by_discord_id,
    sync_channel_history,
    update_chat_message_reactions,
    upsert_discord_user
)
from app.core.context_builder import build_llm_context, build_proactive_context, get_configured_timezone
from app.core.llm_client import execute_llm_with_fallback, clean_response_text
from app.core.event_bus import event_bus, StreamEvent
from app.bot.handlers import setup_slash_commands

logger = logging.getLogger(__name__)

def split_message(text: str, max_chars: int = 1950) -> List[str]:
    """Splits a long message into chunks within Discord's character limit."""
    cleaned = clean_response_text(text)
    if len(cleaned) <= max_chars:
        return [cleaned] if cleaned else []
        
    chunks = []
    current_chunk = ""
    lines = cleaned.split("\n")
    
    for line in lines:
        if len(current_chunk) + len(line) + 1 <= max_chars:
            current_chunk += ("\n" if current_chunk else "") + line
        else:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            # If a single line exceeds max_chars on its own
            if len(line) > max_chars:
                for i in range(0, len(line), max_chars):
                    chunks.append(line[i:i + max_chars])
            else:
                current_chunk = line
                
    if current_chunk:
        chunks.append(current_chunk)
        
    return chunks

class TypingContext:
    """Context manager that keeps the Discord 'is typing' indicator active during long operations."""
    def __init__(self, channel: discord.abc.Messageable):
        self.channel = channel
        self.task: Optional[asyncio.Task] = None

    async def _keep_typing(self):
        try:
            while True:
                await self.channel.typing()
                await asyncio.sleep(8.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Typing indicator notice: {e}")

    async def __aenter__(self):
        self.task = asyncio.create_task(self._keep_typing())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

def _parse_frequency_interval(pattern: str) -> float:
    """Parses frequency string like '2/day', '5/week', '10/month' into seconds with random jitter."""
    import random
    raw = (pattern or "").strip().lower()
    interval = 86400.0
    
    if "/day" in raw or "/giorno" in raw or "/d" in raw:
        num_str = raw.replace("/day", "").replace("/giorno", "").replace("/d", "").strip()
        try:
            n = float(num_str) if num_str else 1.0
            interval = 86400.0 / max(0.1, n)
        except ValueError:
            interval = 86400.0
    elif "/week" in raw or "/settimana" in raw or "/w" in raw:
        num_str = raw.replace("/week", "").replace("/settimana", "").replace("/w", "").strip()
        try:
            n = float(num_str) if num_str else 1.0
            interval = (7.0 * 86400.0) / max(0.1, n)
        except ValueError:
            interval = 7.0 * 86400.0
    elif "/month" in raw or "/mese" in raw or "/m" in raw:
        num_str = raw.replace("/month", "").replace("/mese", "").replace("/m", "").strip()
        try:
            n = float(num_str) if num_str else 1.0
            interval = (30.0 * 86400.0) / max(0.1, n)
        except ValueError:
            interval = 30.0 * 86400.0
    else:
        try:
            n = float(raw)
            interval = 86400.0 / max(0.1, n)
        except ValueError:
            interval = 86400.0
            
    jitter = interval * random.uniform(-0.25, 0.25)
    return max(180.0, interval + jitter)

def _is_within_active_hours(active_hours_str: str) -> bool:
    """Checks if the current local time falls within the configured active hours window (e.g. '09:00-22:00')."""
    if not active_hours_str or "-" not in active_hours_str:
        return True
    try:
        import datetime
        from app.core.context_builder import get_configured_timezone
        parts = active_hours_str.strip().split("-")
        start_h, start_m = map(int, parts[0].strip().split(":"))
        end_h, end_m = map(int, parts[1].strip().split(":"))
        
        tz = get_configured_timezone()
        now = datetime.datetime.now(tz)
        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes <= end_minutes
        else:
            return current_minutes >= start_minutes or current_minutes <= end_minutes
    except Exception:
        return True

class DiscordBotInstance:
    def __init__(self, config: Dict[str, Any]):
        self.bot_id = config["id"]
        self.config = config
        self.user_cooldowns: Dict[str, float] = {}  # user_id -> last_timestamp
        self.consecutive_bot_replies: Dict[str, int] = {}  # channel_id -> count
        self.active_conversations: Dict[str, int] = {}  # channel_id -> messages_left
        self.spontaneous_task: Optional[asyncio.Task] = None
        self.spontaneous_last_run: Dict[str, float] = {}
        self.spontaneous_intervals: Dict[str, float] = {}
        self.status = "stopped"
        self.last_error: Optional[str] = None
        
        # Setup discord client intents (message_content, guilds, and members)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        self.bot_client = commands.Bot(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        
        self._register_events()
        setup_slash_commands(self)

    def update_config(self, new_config: Dict[str, Any]):
        self.config = new_config
        self._start_spontaneous_scheduler()

    def _register_events(self):
        @self.bot_client.event
        async def on_ready():
            self.status = "running"
            self.last_error = None
            logger.info(f"Bot '{self.config.get('name')}' ({self.bot_client.user}) online and ready!")
            self._start_spontaneous_scheduler()
            try:
                synced = await self.bot_client.tree.sync()
                logger.info(f"Bot '{self.config.get('name')}' synced {len(synced)} slash commands.")
            except Exception as e:
                logger.warning(f"Error syncing slash commands for {self.config.get('name')}: {e}")
                
            async def background_sync():
                # Synchronize all server members into discord_users
                for guild in self.bot_client.guilds:
                    try:
                        logger.info(f"Bot '{self.config.get('name')}': syncing members for server '{guild.name}' ({guild.id})...")
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
                        logger.info(f"Bot '{self.config.get('name')}': successfully synced members for '{guild.name}'.")
                    except Exception as e:
                        logger.warning(f"Error syncing members for server {guild.id}: {e}")

                # Run catch-up sync for enabled channels
                enabled_channels = self.config.get("enabled_channels", [])
                for ch_id_str in enabled_channels:
                    try:
                        ch_id = int(ch_id_str)
                        channel = self.bot_client.get_channel(ch_id)
                        if channel and hasattr(channel, "history"):
                            recent_msgs = []
                            async for msg in channel.history(limit=50):
                                recent_msgs.append({
                                    "id": str(msg.id),
                                    "content": msg.content,
                                    "author_id": str(msg.author.id),
                                    "author_name": msg.author.display_name or msg.author.name,
                                    "channel_name": getattr(channel, "name", "DM"),
                                    "has_attachments": len(msg.attachments) > 0,
                                    "reference_message_id": str(msg.reference.message_id) if msg.reference else None,
                                    "is_reply": msg.type == discord.MessageType.reply,
                                    "reactions": [str(r.emoji) for r in msg.reactions],
                                    "timestamp": msg.created_at.replace(tzinfo=None)  # Store naive UTC to match DB format
                                })
                            await sync_channel_history(str(ch_id), recent_msgs, fetch_limit=50)
                    except Exception as e:
                        logger.warning(f"Error during catch-up sync for channel {ch_id_str} on bot {self.bot_id}: {e}")
                        
            asyncio.create_task(background_sync())

        @self.bot_client.event
        async def on_message(message: discord.Message):
            await self._handle_incoming_message(message)

        @self.bot_client.event
        async def on_member_join(member: discord.Member):
            try:
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
            except Exception as e:
                logger.debug(f"Notice on member join sync: {e}")

        @self.bot_client.event
        async def on_message_edit(before: discord.Message, after: discord.Message):
            if after.author.id == self.bot_client.user.id:
                return
            if before.content != after.content:
                await update_chat_message(str(after.id), after.content)
                
        @self.bot_client.event
        async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
            await delete_chat_message_by_discord_id(str(payload.message_id))

        async def handle_raw_reaction(payload: discord.RawReactionActionEvent):
            if payload.user_id == self.bot_client.user.id:
                return
            channel = self.bot_client.get_channel(payload.channel_id)
            if not channel: return
            try:
                msg = await channel.fetch_message(payload.message_id)
                reactions = [str(r.emoji) for r in msg.reactions]
                await update_chat_message_reactions(str(msg.id), reactions)
            except Exception as e:
                logger.debug(f"Error updating reactions for {payload.message_id}: {e}")

        @self.bot_client.event
        async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
            await handle_raw_reaction(payload)

        @self.bot_client.event
        async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
            await handle_raw_reaction(payload)

        @self.bot_client.event
        async def on_member_update(before: discord.Member, after: discord.Member):
            try:
                avatar_url = str(after.display_avatar.url) if hasattr(after, "display_avatar") and after.display_avatar else None
                global_name = getattr(after, "global_name", None)
                await upsert_discord_user(
                    user_id=str(after.id),
                    username=after.name,
                    global_name=global_name,
                    display_name=after.display_name or global_name or after.name,
                    avatar_url=avatar_url,
                    is_bot=after.bot
                )
            except Exception as e:
                logger.debug(f"Notice on member update sync: {e}")

        @self.bot_client.event
        async def on_user_update(before: discord.User, after: discord.User):
            try:
                avatar_url = str(after.display_avatar.url) if hasattr(after, "display_avatar") and after.display_avatar else None
                global_name = getattr(after, "global_name", None)
                await upsert_discord_user(
                    user_id=str(after.id),
                    username=after.name,
                    global_name=global_name,
                    display_name=getattr(after, "display_name", None) or global_name or after.name,
                    avatar_url=avatar_url,
                    is_bot=after.bot
                )
            except Exception as e:
                logger.debug(f"Notice on user update sync: {e}")

    async def _handle_incoming_message(self, message: discord.Message):
        channel_id = str(message.channel.id)
        author_id = str(message.author.id)
        author_name = message.author.display_name or message.author.name
        channel_name = getattr(message.channel, "name", "DM")
        guild_id = str(message.guild.id) if message.guild else None

        # Synchronize Discord User Profile in background
        try:
            avatar_url = str(message.author.display_avatar.url) if hasattr(message.author, "display_avatar") and message.author.display_avatar else None
            global_name = getattr(message.author, "global_name", None)
            await upsert_discord_user(
                user_id=author_id,
                username=message.author.name,
                global_name=global_name,
                display_name=author_name,
                avatar_url=avatar_url,
                is_bot=message.author.bot
            )
        except Exception as e:
            logger.debug(f"Notice on user sync in _handle_incoming_message: {e}")
        
        was_follow_up_active = self.active_conversations.get(channel_id, 0) > 0
            
        # 1. Vision & Attachments
        has_attachments = len(message.attachments) > 0
        image_urls = []
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                try:
                    img_bytes = await att.read()
                    b64_str = base64.b64encode(img_bytes).decode('utf-8')
                    mime = att.content_type
                    image_urls.append(f"data:{mime};base64,{b64_str}")
                except Exception as e:
                    logger.warning(f"Error downloading image attachment: {e}")
            elif any(att.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                try:
                    img_bytes = await att.read()
                    b64_str = base64.b64encode(img_bytes).decode('utf-8')
                    ext = att.filename.lower().split(".")[-1]
                    mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    image_urls.append(f"data:{mime};base64,{b64_str}")
                except Exception as e:
                    logger.warning(f"Error downloading image attachment: {e}")

        # 2. Passive ingestion into FTS5 history
        try:
            await log_chat_message(
                channel_id=channel_id,
                channel_name=channel_name,
                author_id=author_id,
                author_name=author_name,
                content=message.content,
                has_attachments=has_attachments,
                message_id=str(message.id),
                reference_message_id=str(message.reference.message_id) if message.reference else None,
                is_reply=message.type == discord.MessageType.reply,
                reactions=[str(r.emoji) for r in message.reactions]
            )
        except Exception as e:
            logger.error(f"Error saving message to FTS5: {e}")

        # 3. Ignore messages sent by this bot itself
        if message.author.id == self.bot_client.user.id:
            return

        # 4. Anti-Loop Guards
        if message.author.bot:
            if self.config.get("ignore_bots", True):
                return
            # Check consecutive bot replies
            consecutive = self.consecutive_bot_replies.get(channel_id, 0)
            max_consecutive = self.config.get("max_consecutive_bot_replies", 1)
            if consecutive >= max_consecutive:
                logger.debug(f"Anti-loop triggered on channel {channel_id}: max consecutive bot replies limit ({max_consecutive}) reached.")
                return
            self.consecutive_bot_replies[channel_id] = consecutive + 1
        else:
            self.consecutive_bot_replies[channel_id] = 0

        # 5. Whitelist / Blacklist Filters
        enabled_channels = self.config.get("enabled_channels", [])
        if enabled_channels and channel_id not in enabled_channels:
            return
            
        blacklisted_channels = self.config.get("blacklisted_channels", [])
        if blacklisted_channels and channel_id in blacklisted_channels:
            return
            
        blacklisted_users = self.config.get("blacklisted_users", [])
        if blacklisted_users and author_id in blacklisted_users:
            return

        # 6. Evaluate Trigger Rules (in priority order)
        matched_rule = await self._match_trigger_rule(message, was_follow_up_active)
        if not matched_rule:
            return

        # 7. Check Reply Policy associated with the matched rule
        rule_reply_policy = matched_rule.get("reply_policy", "ai_choice")
        if rule_reply_policy == "passive":
            return

        # 8. User Cooldown
        cooldown_sec = self.config.get("cooldown_seconds", 3)
        now = time.time()
        if author_id in self.user_cooldowns:
            elapsed = now - self.user_cooldowns[author_id]
            if elapsed < cooldown_sec:
                logger.debug(f"User {author_name} in cooldown ({elapsed:.1f}s / {cooldown_sec}s)")
                return
        self.user_cooldowns[author_id] = now

        # Decrement follow up counter ONLY IF the matched rule was follow_up
        if matched_rule.get("type") == "follow_up":
            self.active_conversations[channel_id] -= 1

        # 9. AI Execution with typing indicator and trigger-specific reply policy
        await self._process_ai_response(message, image_urls, guild_id, reply_policy=rule_reply_policy)

    async def _match_trigger_rule(self, message: discord.Message, was_follow_up_active: bool = False) -> Optional[Dict[str, Any]]:
        """Evaluates configured trigger rules in priority order and returns the first matching rule."""
        triggers = self.config.get("triggers", [])
        bot_name = self.config.get("name", "").lower()
        content = message.content or ""

        # If no explicit triggers list is configured, fallback to legacy fields
        if not triggers:
            legacy_mode = self.config.get("trigger_mode", "keywords")
            legacy_policy = self.config.get("reply_policy", "ai_choice")
            legacy_kws = self.config.get("trigger_keywords", [])
            case_sensitive = self.config.get("case_sensitive", False)
            triggers = [{
                "type": legacy_mode,
                "pattern": ", ".join(legacy_kws) if isinstance(legacy_kws, list) else str(legacy_kws),
                "case_sensitive": case_sensitive,
                "reply_policy": legacy_policy
            }]

        for rule in triggers:
            rule_type = rule.get("type", "keywords")
            pattern = rule.get("pattern", "").strip()
            case_sensitive = rule.get("case_sensitive", False)

            if rule_type == "always":
                return rule
                
            elif rule_type == "follow_up":
                if was_follow_up_active:
                    return rule

            elif rule_type in ("reply_to_bot", "reply"):
                if message.reference:
                    ref = message.reference.resolved
                    if not isinstance(ref, discord.Message) and message.reference.message_id:
                        try:
                            ref = await message.channel.fetch_message(message.reference.message_id)
                        except Exception:
                            ref = None
                    if isinstance(ref, discord.Message) and ref.author:
                        if self.bot_client.user and ref.author.id == self.bot_client.user.id:
                            return rule

            elif rule_type == "mention":
                if self.bot_client.user in message.mentions:
                    return rule

            elif rule_type == "command":
                cmd_pattern = pattern if pattern else f"/{bot_name}"
                check_content = content if case_sensitive else content.lower()
                target_cmd = cmd_pattern if case_sensitive else cmd_pattern.lower()
                if check_content.startswith(target_cmd) or (self.bot_client.user in message.mentions and not pattern):
                    return rule

            elif rule_type == "keywords":
                if pattern:
                    raw_keywords = [k.strip() for k in pattern.split(",") if k.strip()]
                else:
                    raw_keywords = self.config.get("trigger_keywords", [])
                
                check_content = content if case_sensitive else content.lower()
                for kw in raw_keywords:
                    target_kw = kw if case_sensitive else kw.lower()
                    if target_kw and target_kw in check_content:
                        return rule

        return None

    async def _process_ai_response(self, message: discord.Message, image_urls: List[str], guild_id: Optional[str], reply_policy: str = "ai_choice"):
        channel_id = str(message.channel.id)
        author_id = str(message.author.id)
        author_name = message.author.display_name or message.author.name
        channel_name = getattr(message.channel, "name", "DM")
        
        try:
            async with TypingContext(message.channel):
                # 1. Build Context (system prompt, lore, user memories, recent history)
                context_result = await build_llm_context(
                    bot_id=self.bot_id,
                    system_prompt=self.config.get("system_prompt", "You are an AI assistant on a Discord server."),
                    channel_id=channel_id,
                    channel_name=channel_name,
                    author_id=author_id,
                    author_name=author_name,
                    current_message=message.content,
                    reply_policy=reply_policy,
                    memory_mode=self.config.get("memory_mode", "recent_active"),
                    active_users_count=self.config.get("active_users_count", 5),
                    recent_messages_count=self.config.get("recent_messages_count", 15),
                    attachments=image_urls,
                    guild_id=guild_id,
                    message_id=str(message.id),
                    bot_user_id=str(self.bot_client.user.id) if self.bot_client.user else None
                )
                
                # Context object passed to tools
                tool_context = {
                    "message": message,
                    "channel": message.channel,
                    "guild": message.guild,
                    "guild_id": guild_id,
                    "user_id": author_id,
                    "user_name": author_name,
                    "channel_id": channel_id,
                    "bot_id": self.bot_id,
                    "bot_user_id": str(self.bot_client.user.id)
                }
                
                # 2. Execute LLM Priority Fallback Chain with tool calling
                request_id = event_bus.new_request_id()
                
                # Publish stream_start event
                await event_bus.publish(StreamEvent(
                    "stream_start",
                    {
                        "bot_id": self.bot_id,
                        "bot_name": self.config.get("name", "Unknown"),
                        "user_id": author_id,
                        "user_name": author_name,
                        "channel_id": channel_id,
                        "channel_name": channel_name
                    },
                    request_id
                ))
                
                llm_result = await execute_llm_with_fallback(
                    endpoint_ids=self.config.get("endpoint_chain", []),
                    messages=context_result.messages,
                    enable_tools=True,
                    context=tool_context,
                    stream=True,
                    request_id=request_id
                )
                
                # Publish stream_end event
                await event_bus.publish(StreamEvent(
                    "stream_end",
                    {
                        "model_used": llm_result.model_used,
                        "prompt_tokens": llm_result.prompt_tokens,
                        "completion_tokens": llm_result.completion_tokens,
                        "total_tokens": llm_result.total_tokens,
                        "refused": llm_result.refused,
                        "error": llm_result.error,
                        "tools_count": len(llm_result.tools_called)
                    },
                    request_id
                ))
                
                # Extract system prompt and user input string for audit logging
                system_prompt = next((m["content"] for m in context_result.messages if m["role"] == "system"), "")
                input_text = "\n".join([f"[{m['role']}] {m['content']}" for m in context_result.messages if m["role"] != "system"])

                # 3. Log Audit & Stats
                await log_audit(
                    bot_id=self.bot_id,
                    user_id=author_id,
                    channel_id=channel_id,
                    model_used=llm_result.model_used,
                    prompt_tokens=llm_result.prompt_tokens,
                    completion_tokens=llm_result.completion_tokens,
                    total_tokens=llm_result.total_tokens,
                    tools_called=llm_result.tools_called,
                    refused=llm_result.refused,
                    input_text=input_text,
                    output_text=llm_result.text if not llm_result.refused else "[REFUSED]",
                    error_message=llm_result.error,
                    system_prompt=system_prompt
                )
                
                # 4. Intelligent Silence [REFUSE]
                if llm_result.refused:
                    logger.info(f"Bot '{self.config.get('name')}' chose to remain silent [REFUSE].")
                    return
                    
                # 5. If all endpoints failed without producing output, do not send internal error dumps to chat
                if llm_result.error and not llm_result.text:
                    logger.error(f"Request failed for '{self.config.get('name')}': {llm_result.error}")
                    return

                # 6. Send Reply (handling long messages > 1950 characters and stripping thinking tokens)
                reply_text = clean_response_text(llm_result.text)
                if reply_text:
                    chunks = split_message(reply_text)
                    for idx, chunk in enumerate(chunks):
                        if idx == 0:
                            await message.reply(chunk, mention_author=False)
                        else:
                            await message.channel.send(chunk)
                            
                    # Reset follow up counter if rule exists
                    follow_up_rule = next((r for r in self.config.get("triggers", []) if r.get("type") == "follow_up"), None)
                    if follow_up_rule:
                        try:
                            self.active_conversations[channel_id] = int(follow_up_rule.get("pattern", "3"))
                        except ValueError:
                            pass
                            
        except Exception as e:
            logger.error(f"Error during AI processing for {self.config.get('name')}: {e}", exc_info=True)
            self.last_error = str(e)
            
            # Log the error
            try:
                system_prompt_str = next((m["content"] for m in context_result.messages if m["role"] == "system"), "") if 'context_result' in locals() else ""
                input_text_str = "\n".join([f"[{m['role']}] {m['content']}" for m in context_result.messages if m["role"] != "system"]) if 'context_result' in locals() else ""
                
                await log_audit(
                    bot_id=self.bot_id,
                    user_id=author_id,
                    channel_id=channel_id,
                    model_used="error",
                    error_message=str(e),
                    system_prompt=system_prompt_str,
                    input_text=input_text_str,
                    output_text=None
                )
            except Exception as inner_e:
                logger.error(f"Failed to log audit error: {inner_e}")

    def _start_spontaneous_scheduler(self):
        """Starts or restarts the background proactive scheduler task."""
        if self.spontaneous_task and not self.spontaneous_task.done():
            self.spontaneous_task.cancel()
        self.spontaneous_task = asyncio.create_task(self._spontaneous_scheduler_loop())

    async def _spontaneous_scheduler_loop(self):
        """Background loop evaluating spontaneous proactive opportunities."""
        # Initial boot delay to let bot stabilize
        await asyncio.sleep(2.0)
        
        while not self.bot_client.is_closed():
            try:
                triggers = self.config.get("triggers", [])
                spontaneous_rules = [r for r in triggers if r.get("type") == "spontaneous"]
                
                if spontaneous_rules and self.status == "running":
                    for rule in spontaneous_rules:
                        try:
                            await self._evaluate_spontaneous_rule(rule)
                        except Exception as e:
                            logger.error(f"Error evaluating spontaneous rule on bot {self.bot_id}: {e}", exc_info=True)
                
                # Check cycle every 2 minutes
                await asyncio.sleep(120.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in spontaneous scheduler loop for bot {self.bot_id}: {e}")
                await asyncio.sleep(60.0)

    async def _evaluate_spontaneous_rule(self, rule: Dict[str, Any]):
        pattern_str = rule.get("pattern", "1/day")
        rule_key = f"{pattern_str}_{rule.get('channel_id', '')}_{rule.get('topic', '')}"
        now = time.time()
        
        # Initialize tracking for new rule
        if rule_key not in self.spontaneous_intervals:
            self.spontaneous_intervals[rule_key] = _parse_frequency_interval(pattern_str)
            self.spontaneous_last_run[rule_key] = now
            return
            
        last_run = self.spontaneous_last_run.get(rule_key, 0)
        target_interval = self.spontaneous_intervals.get(rule_key, 86400.0)
        if (now - last_run) < target_interval:
            return
            
        # Check active hours window
        active_hours = rule.get("active_hours", "09:00-23:00")
        if not _is_within_active_hours(active_hours):
            return
            
        # Update run timestamp and compute next jittered interval
        self.spontaneous_last_run[rule_key] = now
        self.spontaneous_intervals[rule_key] = _parse_frequency_interval(pattern_str)
        
        # Resolve target channel
        target_channel = None
        channel_id_spec = rule.get("channel_id")
        if channel_id_spec:
            try:
                target_channel = self.bot_client.get_channel(int(str(channel_id_spec).strip("<#> ")))
            except Exception:
                pass
                
        if not target_channel:
            enabled_channels = self.config.get("enabled_channels", [])
            for ch_id_str in enabled_channels:
                try:
                    ch = self.bot_client.get_channel(int(ch_id_str))
                    if ch and hasattr(ch, "send"):
                        target_channel = ch
                        break
                except Exception:
                    pass
                    
        if not target_channel:
            for guild in self.bot_client.guilds:
                for ch in guild.text_channels:
                    perms = ch.permissions_for(guild.me)
                    if perms.send_messages and perms.view_channel:
                        target_channel = ch
                        break
                if target_channel:
                    break
                    
        if not target_channel:
            logger.debug(f"Spontaneous check skipped for {self.config.get('name')}: no accessible text channel found.")
            return

        channel_id = str(target_channel.id)
        channel_name = getattr(target_channel, "name", "general")
        guild_id = str(target_channel.guild.id) if hasattr(target_channel, "guild") and target_channel.guild else None
        
        logger.info(f"Bot '{self.config.get('name')}' evaluating spontaneous opportunity in #{channel_name} (interval: {target_interval:.0f}s)...")
        
        # Build proactive context
        context_result = await build_proactive_context(
            bot_id=self.bot_id,
            system_prompt=self.config.get("system_prompt", "You are an AI assistant on a Discord server."),
            channel_id=channel_id,
            channel_name=channel_name,
            topic_guidance=rule.get("topic") or pattern_str,
            guild_id=guild_id,
            bot_user_id=str(self.bot_client.user.id) if self.bot_client.user else None,
            bot_name=self.config.get("name", "Assistant"),
            enabled_channels=self.config.get("enabled_channels", []),
            recent_messages_count=self.config.get("recent_messages_count", 15)
        )
        
        tool_context = {
            "channel": target_channel,
            "guild": target_channel.guild if hasattr(target_channel, "guild") else None,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "bot_id": self.bot_id,
            "bot_user_id": str(self.bot_client.user.id) if self.bot_client.user else None
        }
        
        request_id = event_bus.new_request_id()
        await event_bus.publish(StreamEvent(
            "stream_start",
            {
                "bot_id": self.bot_id,
                "bot_name": self.config.get("name", "Unknown"),
                "channel_id": channel_id,
                "channel_name": channel_name,
                "trigger_type": "spontaneous"
            },
            request_id
        ))
        
        llm_result = await execute_llm_with_fallback(
            endpoint_ids=self.config.get("endpoint_chain", []),
            messages=context_result.messages,
            enable_tools=True,
            context=tool_context,
            stream=True,
            request_id=request_id
        )
        
        system_prompt_str = next((m["content"] for m in context_result.messages if m["role"] == "system"), "")
        input_text_str = "\n".join([f"[{m['role']}] {m['content']}" for m in context_result.messages if m["role"] != "system"])
        
        clean_text = clean_response_text(llm_result.text)
        is_refused = llm_result.refused or (clean_text == "[REFUSE]")
        
        await log_audit(
            bot_id=self.bot_id,
            user_id="spontaneous_trigger",
            channel_id=channel_id,
            model_used=llm_result.model_used,
            prompt_tokens=llm_result.prompt_tokens,
            completion_tokens=llm_result.completion_tokens,
            total_tokens=llm_result.total_tokens,
            tools_called=llm_result.tools_called,
            refused=is_refused,
            input_text=input_text_str,
            output_text=clean_text if not is_refused else "[REFUSED] (Spontaneous AI Decision)",
            error_message=llm_result.error,
            system_prompt=system_prompt_str
        )
        
        if is_refused or not clean_text:
            logger.info(f"Bot '{self.config.get('name')}' evaluated spontaneous opportunity in #{channel_name} and chose to stay silent [REFUSE].")
            return
            
        chunks = split_message(clean_text)
        for chunk in chunks:
            sent_msg = await target_channel.send(chunk)
            await log_chat_message(
                channel_id=channel_id,
                channel_name=channel_name,
                author_id=str(self.bot_client.user.id),
                author_name=self.config.get("name", "Assistant"),
                content=chunk,
                message_id=str(sent_msg.id)
            )
        logger.info(f"Bot '{self.config.get('name')}' sent spontaneous proactive message to #{channel_name}.")

