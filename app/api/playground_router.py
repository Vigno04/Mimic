import uuid
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks

# ... (imports handled internally by the replacement, I will redefine it fully)
import json
import time
import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select
from app.database.models import BotModel
from app.database.session import get_db, AsyncSession
from app.core.context_builder import build_llm_context
from app.core.llm_client import execute_llm_with_fallback
from app.core.event_bus import event_bus, StreamEvent

router = APIRouter(prefix="/api/playground", tags=["playground"])

class PlaygroundMessageRequest(BaseModel):
    bot_id: str
    message: str
    request_id: Optional[str] = None
    username: str = "TestUser"
    user_id: str = "simulated_user_123"
    channel_name: str = "playground-testing"
    channel_id: str = "simulated_chan_999"
    guild_id: Optional[str] = "simulated_guild_1"
    image_url: Optional[str] = None
    override_reply_policy: Optional[str] = None

async def _run_playground_llm(
    request_id: str,
    bot_dict: dict,
    bot_id: str,
    payload: PlaygroundMessageRequest,
    reply_policy: str
):
    try:
        attachments = [payload.image_url] if payload.image_url else None
        start_time = time.time()
        
        # 2. Build Context
        context_res = await build_llm_context(
            bot_id=bot_id,
            system_prompt=bot_dict.get("system_prompt", "You are an AI assistant."),
            channel_id=payload.channel_id,
            channel_name=payload.channel_name,
            author_id=payload.user_id,
            author_name=payload.username,
            current_message=payload.message,
            reply_policy=reply_policy,
            memory_mode=bot_dict.get("memory_mode", "recent_active"),
            active_users_count=bot_dict.get("active_users_count", 5),
            recent_messages_count=bot_dict.get("recent_messages_count", 15),
            attachments=attachments,
            guild_id=payload.guild_id,
            bot_user_id=bot_id
        )
        
        # 3. Simulated Tool Context
        tool_context = {
            "user_id": payload.user_id,
            "user_name": payload.username,
            "channel_id": payload.channel_id,
            "guild_id": payload.guild_id,
            "bot_id": bot_id,
            "bot_user_id": bot_id
        }
        
        # 4. Run LLM with Priority Fallback Chain
        llm_res = await execute_llm_with_fallback(
            endpoint_ids=bot_dict.get("endpoint_chain", []),
            messages=context_res.messages,
            enable_tools=True,
            context=tool_context,
            stream=True,
            request_id=request_id
        )
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        # Emit stream_end event
        await event_bus.publish(StreamEvent(
            "stream_end",
            {
                "status": "success" if not llm_res.error else "error",
                "reply": llm_res.text if not llm_res.refused else "(Intelligent silence: bot chose not to reply [REFUSE])",
                "raw_text": llm_res.text,
                "refused": llm_res.refused,
                "error": llm_res.error,
                "elapsed_ms": elapsed_ms,
                "debug_inspector": {
                    "model_used": llm_res.model_used,
                    "endpoint_id": llm_res.endpoint_id,
                    "tokens": {
                        "prompt": llm_res.prompt_tokens,
                        "completion": llm_res.completion_tokens,
                        "total": llm_res.total_tokens
                    },
                    "tools_called": llm_res.tools_called,
                    "system_prompt": context_res.system_prompt,
                    "injected_server_memories": context_res.injected_server_memories,
                    "injected_user_memories": context_res.injected_user_memories,
                    "messages_payload": context_res.messages
                }
            },
            request_id
        ))
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Playground execution failed: {e}")
        await event_bus.publish(StreamEvent(
            "stream_end",
            {
                "status": "error",
                "error": str(e),
                "debug_inspector": {}
            },
            request_id
        ))

@router.post("/chat")
async def playground_chat(payload: PlaygroundMessageRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # 1. Load Bot config
    stmt = select(BotModel).where(BotModel.id == payload.bot_id)
    res = await db.execute(stmt)
    bot = res.scalars().first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found.")

    bot_dict = bot.to_dict()
    
    # Resolve reply_policy from triggers list or override
    reply_policy = payload.override_reply_policy
    if not reply_policy:
        triggers = bot_dict.get("triggers", [])
        content = payload.message.lower()
        matched_policy = None
        for rule in triggers:
            rtype = rule.get("type")
            pattern = (rule.get("pattern") or "").lower()
            if rtype == "always":
                matched_policy = rule.get("reply_policy")
                break
            elif rtype == "command" and (content.startswith(pattern) if pattern else content.startswith(f"/{bot.name.lower()}")):
                matched_policy = rule.get("reply_policy")
                break
            elif rtype in ("reply_to_bot", "reply") and ("reply" in content or "re:" in content):
                matched_policy = rule.get("reply_policy")
                break
            elif rtype == "mention" and f"@{bot.name.lower()}" in content:
                matched_policy = rule.get("reply_policy")
                break
            elif rtype == "keywords":
                kws = [k.strip() for k in pattern.split(",") if k.strip()]
                if any(k in content for k in kws):
                    matched_policy = rule.get("reply_policy")
                    break
        reply_policy = matched_policy or bot_dict.get("reply_policy", "ai_choice")

    request_id = payload.request_id or event_bus.new_request_id()
    
    background_tasks.add_task(
        _run_playground_llm,
        request_id=request_id,
        bot_dict=bot_dict,
        bot_id=bot.id,
        payload=payload,
        reply_policy=reply_policy
    )
    
    # Publish start event
    await event_bus.publish(StreamEvent(
        "stream_start",
        {
            "bot_name": bot.name,
            "bot_id": bot.id,
            "user_name": payload.username,
            "user_id": payload.user_id,
            "channel_name": payload.channel_name,
            "channel_id": payload.channel_id,
            "is_playground": True
        },
        request_id
    ))
    
    return {"status": "started", "request_id": request_id}
