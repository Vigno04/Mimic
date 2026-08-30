import datetime
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    DateTime,
    Text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

def get_utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

class EndpointModel(Base):
    __tablename__ = "endpoints"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False, default="openai")  # openai, gemini, anthropic, ollama, custom
    base_url = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    model_name = Column(String, nullable=False)
    endpoint_standard = Column(String, nullable=False, default="completions") # completions, responses
    is_global_fallback = Column(Boolean, default=False)
    created_at = Column(DateTime, default=get_utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model_name": self.model_name,
            "endpoint_standard": self.endpoint_standard or "completions",
            "is_global_fallback": bool(self.is_global_fallback),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class BotModel(Base):
    __tablename__ = "bots"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    discord_token = Column(String, nullable=False)
    endpoint_chain = Column(Text, nullable=False, default="[]")  # JSON Array of endpoint IDs in order
    system_prompt = Column(Text, nullable=False, default="You are a friendly and intelligent AI assistant.")
    enabled_channels = Column(Text, nullable=True, default="[]")  # JSON Array of channel IDs
    blacklisted_channels = Column(Text, nullable=True, default="[]")  # JSON Array
    blacklisted_users = Column(Text, nullable=True, default="[]")  # JSON Array
    triggers = Column(Text, nullable=True, default="[]")  # JSON Array of trigger rules: [{"type": "command"|"keywords"|"mention"|"always", "pattern": "...", "case_sensitive": false, "reply_policy": "mandatory"|"ai_choice"|"passive"}]
    trigger_keywords = Column(Text, nullable=True, default="[]")  # JSON Array (legacy)
    case_sensitive = Column(Boolean, default=False)  # (legacy)
    trigger_mode = Column(String, default="keywords")  # (legacy)
    reply_policy = Column(String, default="ai_choice")  # (legacy)
    memory_mode = Column(String, default="recent_active")  # 'full', 'recent_active', 'tool_only'
    active_users_count = Column(Integer, default=5)
    recent_messages_count = Column(Integer, default=15)
    cooldown_seconds = Column(Integer, default=3)
    ignore_bots = Column(Boolean, default=True)
    max_consecutive_bot_replies = Column(Integer, default=1)
    is_running = Column(Boolean, default=False)

    def to_dict(self):
        import json
        raw_triggers = json.loads(self.triggers) if self.triggers else []
        # If no explicit triggers list, build from legacy fields
        if not raw_triggers:
            legacy_mode = self.trigger_mode or "keywords"
            legacy_policy = self.reply_policy or "ai_choice"
            legacy_kws = json.loads(self.trigger_keywords) if self.trigger_keywords else []
            if legacy_mode == "keywords":
                raw_triggers = [{
                    "type": "keywords",
                    "pattern": ", ".join(legacy_kws),
                    "case_sensitive": bool(self.case_sensitive),
                    "reply_policy": legacy_policy
                }]
            elif legacy_mode == "mention":
                raw_triggers = [{
                    "type": "mention",
                    "pattern": "",
                    "case_sensitive": False,
                    "reply_policy": legacy_policy
                }]
            elif legacy_mode == "command":
                raw_triggers = [{
                    "type": "command",
                    "pattern": f"/{self.name.lower()}",
                    "case_sensitive": False,
                    "reply_policy": legacy_policy
                }]
            elif legacy_mode == "always":
                raw_triggers = [{
                    "type": "always",
                    "pattern": "",
                    "case_sensitive": False,
                    "reply_policy": legacy_policy
                }]

        return {
            "id": self.id,
            "name": self.name,
            "discord_token": self.discord_token,
            "endpoint_chain": json.loads(self.endpoint_chain) if self.endpoint_chain else [],
            "system_prompt": self.system_prompt,
            "triggers": raw_triggers,
            "enabled_channels": json.loads(self.enabled_channels) if self.enabled_channels else [],
            "blacklisted_channels": json.loads(self.blacklisted_channels) if self.blacklisted_channels else [],
            "blacklisted_users": json.loads(self.blacklisted_users) if self.blacklisted_users else [],
            "trigger_keywords": json.loads(self.trigger_keywords) if self.trigger_keywords else [],
            "case_sensitive": bool(self.case_sensitive),
            "trigger_mode": self.trigger_mode,
            "reply_policy": self.reply_policy,
            "memory_mode": self.memory_mode,
            "active_users_count": self.active_users_count,
            "recent_messages_count": self.recent_messages_count,
            "cooldown_seconds": self.cooldown_seconds,
            "ignore_bots": bool(self.ignore_bots),
            "max_consecutive_bot_replies": self.max_consecutive_bot_replies,
            "is_running": bool(self.is_running),
        }

class ServerMemoryModel(Base):
    __tablename__ = "server_memories"

    id = Column(String, primary_key=True)
    bot_id = Column(String, nullable=True, index=True)
    guild_id = Column(String, nullable=True)
    key_phrase = Column(String, nullable=False)
    fact = Column(Text, nullable=False)
    category = Column(String, nullable=True, default="general")
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "guild_id": self.guild_id,
            "key_phrase": self.key_phrase,
            "fact": self.fact,
            "category": self.category or "general",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class DiscordUserModel(Base):
    __tablename__ = "discord_users"

    id = Column(String, primary_key=True)  # Discord numeric user ID
    username = Column(String, nullable=False, default="")  # Global account handle (e.g. alex_dev)
    global_name = Column(String, nullable=True)  # Global display name (e.g. Alex)
    display_name = Column(String, nullable=True)  # Server nickname / active display name (e.g. Alex [Dev])
    avatar_url = Column(String, nullable=True)  # Discord CDN Avatar URL
    is_bot = Column(Boolean, default=False)
    first_seen = Column(DateTime, default=get_utc_now)
    last_seen = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "global_name": self.global_name,
            "display_name": self.display_name or self.global_name or self.username or self.id,
            "avatar_url": self.avatar_url,
            "is_bot": bool(self.is_bot),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class UserMemoryModel(Base):
    __tablename__ = "user_memories"

    id = Column(String, primary_key=True)
    bot_id = Column(String, nullable=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    username = Column(String, nullable=False)
    fact = Column(Text, nullable=False)
    category = Column(String, nullable=True, default="general")
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "username": self.username,
            "fact": self.fact,
            "category": self.category or "general",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True)
    channel_id = Column(String, nullable=False)
    channel_name = Column(String, nullable=True)
    author_id = Column(String, nullable=False)
    author_name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    has_attachments = Column(Boolean, default=False)
    attachment_urls = Column(Text, nullable=True, default="[]")  # JSON Array of CDN URLs for vision re-use
    reference_message_id = Column(String, nullable=True)
    is_reply = Column(Boolean, default=False)
    reactions = Column(Text, nullable=True, default="[]")
    timestamp = Column(DateTime, default=get_utc_now)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "has_attachments": bool(self.has_attachments),
            "attachment_urls": json.loads(self.attachment_urls) if self.attachment_urls else [],
            "reference_message_id": self.reference_message_id,
            "is_reply": bool(self.is_reply),
            "reactions": json.loads(self.reactions) if self.reactions else [],
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True)
    bot_id = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    channel_id = Column(String, nullable=True)
    model_used = Column(String, nullable=True)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    tools_called = Column(Text, nullable=True, default="[]")  # JSON Array
    refused = Column(Boolean, default=False)
    input_text = Column(Text, nullable=True)
    output_text = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=get_utc_now)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "model_used": self.model_used,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tools_called": json.loads(self.tools_called) if self.tools_called else [],
            "refused": bool(self.refused),
            "input_text": self.input_text,
            "output_text": self.output_text,
            "error_message": self.error_message,
            "system_prompt": self.system_prompt,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

class GlobalSettingsModel(Base):
    __tablename__ = "global_settings"

    id = Column(String, primary_key=True, default="default")
    web_search_provider = Column(String, nullable=False, default="duckduckgo") # 'duckduckgo' or 'searxng'
    searxng_url = Column(String, nullable=True, default="")
    max_search_results = Column(Integer, default=5)
    search_safesearch = Column(String, default="moderate") # 'strict', 'moderate', 'off'
    max_tool_iterations = Column(Integer, default=6)
    
    def to_dict(self):
        return {
            "id": self.id,
            "web_search_provider": self.web_search_provider,
            "searxng_url": self.searxng_url,
            "max_search_results": self.max_search_results,
            "search_safesearch": self.search_safesearch,
            "max_tool_iterations": self.max_tool_iterations
        }
