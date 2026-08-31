import json
import logging
import asyncio
import re
import httpx
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import select
from app.database.models import EndpointModel, GlobalSettingsModel
from app.database.session import AsyncSessionLocal
from app.tools.registry import AVAILABLE_TOOLS, dispatch_tool_call
from app.core.event_bus import event_bus, StreamEvent

logger = logging.getLogger(__name__)

def clean_response_text(text: str) -> str:
    """Strips reasoning/thinking blocks (<think>, <thought>, etc.) and cleans final text output."""
    if not text:
        return ""
        
    raw = text.strip()
    
    # 1. Remove complete blocks <think>...</think>, <thought>...</thought>
    cleaned = re.sub(r'<(think|thought)>.*?</\1>', '', raw, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # 2. Remove square bracket blocks [thinking]...[/thinking]
    if cleaned:
        cleaned = re.sub(r'\[(thinking|thought)\].*?\[/\1\]', '', cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # 3. Remove truncated blocks (where the closing tag is missing)
    if cleaned:
        cleaned = re.sub(r'<(think|thought)>.*$', '', cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
        cleaned = re.sub(r'\[(thinking|thought)\].*$', '', cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # 4. Remove any residual tags
    if cleaned:
        cleaned = re.sub(r'</?(think|thought)>', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'\[/?(thinking|thought)\]', '', cleaned, flags=re.IGNORECASE).strip()
    
    return cleaned.strip()

def extract_text_tool_calls(text: str, reasoning: str = "") -> List[Dict[str, Any]]:
    """Extracts XML/JSON formatted tool calls (e.g. <tool_call>...</tool_call>) for models that do not emit structured deltas."""
    combined = (text or "") + "\n" + (reasoning or "")
    if not combined.strip():
        return []
        
    found_calls = []
    patterns = [
        r'<tool_call>\s*({.*?})\s*</tool_call>',
        r'<function_call>\s*({.*?})\s*</function_call>',
        r'<call:(\w+)>\s*({.*?})\s*</call:\1>'
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, combined, flags=re.DOTALL | re.IGNORECASE):
            try:
                raw_json = match.group(1) if len(match.groups()) == 1 else match.group(2)
                func_name = match.group(1) if len(match.groups()) == 2 else None
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    name = func_name or parsed.get("name")
                    args = parsed.get("arguments", parsed.get("parameters", {}))
                    if name:
                        found_calls.append({
                            "id": f"text_call_{len(found_calls)}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else str(args)
                            }
                        })
            except Exception:
                continue
    return found_calls

class LLMExecutionResult:
    def __init__(
        self,
        text: str,
        refused: bool = False,
        tools_called: Optional[List[Dict[str, Any]]] = None,
        model_used: str = "unknown",
        endpoint_id: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        error: Optional[str] = None
    ):
        self.text = text if not refused else ""
        self.refused = refused
        self.tools_called = tools_called or []
        self.model_used = model_used
        self.endpoint_id = endpoint_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.error = error

    def to_dict(self):
        return {
            "text": self.text,
            "refused": self.refused,
            "tools_called": self.tools_called,
            "model_used": self.model_used,
            "endpoint_id": self.endpoint_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "error": self.error
        }

async def get_endpoints_by_ids(endpoint_ids: List[str]) -> List[EndpointModel]:
    if not endpoint_ids:
        return []
    async with AsyncSessionLocal() as session:
        stmt = select(EndpointModel).where(EndpointModel.id.in_(endpoint_ids))
        res = await session.execute(stmt)
        endpoints_map = {e.id: e for e in res.scalars().all()}
        # Preserve original order
        return [endpoints_map[eid] for eid in endpoint_ids if eid in endpoints_map]

async def get_global_fallback_endpoints() -> List[EndpointModel]:
    async with AsyncSessionLocal() as session:
        stmt = select(EndpointModel).where(EndpointModel.is_global_fallback == True)
        res = await session.execute(stmt)
        return list(res.scalars().all())

async def test_endpoint_connectivity(endpoint: EndpointModel) -> Dict[str, Any]:
    """Tests connectivity and latency of an endpoint."""
    start_time = asyncio.get_event_loop().time()
    test_messages = [
        {"role": "system", "content": "You are a test assistant. Answer with 'OK'."},
        {"role": "user", "content": "ping"}
    ]
    try:
        res = await _call_single_endpoint(endpoint, test_messages, enable_tools=False, max_tokens=10)
        latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
        if res.error:
            return {"status": "error", "message": res.error, "latency_ms": latency_ms}
        return {"status": "online", "message": res.text.strip(), "latency_ms": latency_ms, "model": endpoint.model_name}
    except Exception as e:
        latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
        return {"status": "error", "message": str(e), "latency_ms": latency_ms}

async def _call_single_endpoint(
    endpoint: EndpointModel,
    messages: List[Dict[str, Any]],
    enable_tools: bool = True,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
    stream: bool = False,
    request_id: Optional[str] = None
) -> LLMExecutionResult:
    """Executes a call to a single LLM endpoint and handles the tool call execution loop."""
    provider = (endpoint.provider or "openai").lower()
    base_url = endpoint.base_url
    api_key = endpoint.api_key or ""
    model_name = endpoint.model_name
    
    # Configure base_url defaults if empty
    if not base_url:
        if provider in ["openai", "openai_compatible"]:
            base_url = "https://api.openai.com/v1"
        elif provider == "gemini":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif provider == "anthropic":
            base_url = "https://api.anthropic.com/v1"
        else:
            base_url = "https://api.openai.com/v1"

    base_url = base_url.rstrip("/")
    active_tools = tools if enable_tools else None
    
    # For Anthropic native format vs OpenAI-compatible format
    if provider == "anthropic" and not base_url.endswith("/v1"):
        return await _call_anthropic_native(endpoint, messages, active_tools, context, max_tokens)
    else:
        # Use streaming if requested and there are SSE subscribers
        if stream and request_id:
            return await _call_openai_compatible_stream(endpoint, base_url, api_key, model_name, messages, active_tools, context, max_tokens, request_id, max_iterations=context.get("max_iterations", 6) if context else 6)
        return await _call_openai_compatible(endpoint, base_url, api_key, model_name, messages, active_tools, context, max_tokens, max_iterations=context.get("max_iterations", 6) if context else 6)

def convert_to_responses_payload(payload: dict) -> dict:
    """
    Convert Chat Completions payload to Responses API format (matching OpenWebUI standard).
    Chat Completions: { messages: [{role, content: [{type: 'text'|'image_url', ...}]}], ... }
    Responses API:    { input: [{type: 'message', role, content: [{type: 'input_text'|'input_image', ...}]}], instructions: "..." }
    """
    messages = list(payload.pop('messages', []))
    system_content = ''
    input_items = []

    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')

        if role == 'system':
            if isinstance(content, str):
                system_content = content
            elif isinstance(content, list):
                system_content = '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') in ('text', 'input_text'))
            continue
            
        if role == 'tool':
            input_items.append({
                'type': 'function_call_output',
                'call_id': msg.get('tool_call_id', ''),
                'output': str(content)
            })
            continue

        if role == 'assistant' and msg.get('tool_calls'):
            if content:
                input_items.append({
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': str(content)}]
                })
            for tc in msg.get('tool_calls', []):
                input_items.append({
                    'type': 'function_call',
                    'id': tc.get('id', ''),
                    'name': tc.get('function', {}).get('name', ''),
                    'arguments': tc.get('function', {}).get('arguments', '{}')
                })
            continue

        text_type = 'output_text' if role == 'assistant' else 'input_text'

        if isinstance(content, str):
            content_parts = [{'type': text_type, 'text': content}]
        elif isinstance(content, list):
            content_parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                p_type = part.get('type')
                if p_type in ('text', 'input_text'):
                    content_parts.append({'type': text_type, 'text': part.get('text', '')})
                elif p_type in ('image_url', 'input_image', 'input_file', 'video_url', 'audio_url', 'file_url'):
                    url_data = part.get(p_type, part.get('image_url', {}))
                    if isinstance(url_data, dict):
                        url = url_data.get('url', '')
                        detail = url_data.get('detail') or 'auto'
                    else:
                        url = url_data if isinstance(url_data, str) else ''
                        detail = 'auto'
                        
                    if url.startswith("data:video/") or url.startswith("data:audio/") or p_type in ('input_file', 'video_url', 'audio_url', 'file_url'):
                        content_parts.append({
                            'type': 'input_file',
                            'file_data': url
                        })
                    else:
                        content_parts.append({
                            'type': 'input_image',
                            'image_url': url
                        })
        else:
            content_parts = [{'type': text_type, 'text': str(content)}]

        input_items.append({'type': 'message', 'role': role, 'content': content_parts})

    responses_payload = {**payload, 'input': input_items}
    if system_content:
        responses_payload['instructions'] = system_content

    if 'max_tokens' in responses_payload:
        responses_payload['max_output_tokens'] = responses_payload.pop('max_tokens')

    return responses_payload


async def _call_openai_compatible(
    endpoint: EndpointModel,
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    context: Optional[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    max_iterations: int = 6
) -> LLMExecutionResult:
    """Handles OpenAI-compatible format calls (OpenAI, Gemini OpenAI-compat, Ollama /v1, OpenRouter, vLLM)."""
    headers = {
        "Content-Type": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url_path = "/responses" if getattr(endpoint, "endpoint_standard", "completions") == "responses" else "/chat/completions"
    url = f"{base_url}{url_path}"
    is_responses = getattr(endpoint, "endpoint_standard", "completions") == "responses"
    current_messages = list(messages)
    tools_executed: List[Dict[str, Any]] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    
    iteration = 0
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        while iteration < max_iterations:
            iteration += 1
            if is_responses:
                base_p = {
                    "model": model_name,
                    "messages": current_messages
                }
                if max_tokens is not None:
                    base_p["max_tokens"] = max_tokens
                if tools:
                    base_p["tools"] = tools
                payload = convert_to_responses_payload(base_p)
            else:
                payload = {
                    "model": model_name,
                    "messages": current_messages,
                    "temperature": 0.7
                }
                if max_tokens is not None:
                    payload["max_tokens"] = max_tokens
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    err_msg = f"HTTP {resp.status_code}: {resp.text}"
                    logger.warning(f"Endpoint {endpoint.name} ({model_name}) error: {err_msg}")
                    return LLMExecutionResult(
                        text="",
                        error=err_msg,
                        model_used=model_name,
                        endpoint_id=endpoint.id
                    )
                    
                data = resp.json()
                usage = data.get("usage", {})
                total_prompt_tokens += usage.get("prompt_tokens", 0)
                total_completion_tokens += usage.get("completion_tokens", 0)
                
                content = ""
                tool_calls = []
                
                if "output" in data and isinstance(data["output"], list):
                    for item in data["output"]:
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get("type")
                        if item_type == "message":
                            msg_content = item.get("content", "")
                            if isinstance(msg_content, str):
                                content += msg_content
                            elif isinstance(msg_content, list):
                                for part in msg_content:
                                    if isinstance(part, str):
                                        content += part
                                    elif isinstance(part, dict):
                                        if part.get("type") in ("output_text", "text"):
                                            content += part.get("text", "")
                            if "tool_calls" in item and isinstance(item["tool_calls"], list):
                                tool_calls.extend(item["tool_calls"])
                        elif item_type in ("function_call", "tool_call"):
                            tool_calls.append({
                                "id": item.get("id", f"call_{len(tool_calls)}"),
                                "function": {
                                    "name": item.get("name") or item.get("function", {}).get("name"),
                                    "arguments": item.get("arguments") or item.get("function", {}).get("arguments", "{}")
                                }
                            })
                elif "choices" in data and isinstance(data["choices"], list) and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    message_obj = choice.get("message", {})
                    content = message_obj.get("content") or ""
                    tool_calls = message_obj.get("tool_calls", [])
                else:
                    content = data.get("text", "") or str(data)
                
                # Check if there are tool calls to execute
                if tool_calls and tools:
                    # Append assistant tool call message to history
                    current_messages.append({
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": tool_calls
                    })
                    
                    # Execute all requested tools in parallel or sequence
                    for tc in tool_calls:
                        func_info = tc.get("function", {})
                        func_name = func_info.get("name")
                        func_args_str = func_info.get("arguments", "{}")
                        try:
                            func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                        except Exception:
                            func_args = {}
                            
                        # Dispatch tool execution
                        tool_result = await dispatch_tool_call(func_name, func_args, context)
                        if isinstance(tool_result, dict) and tool_result.get("status") == "error":
                            logger.warning(f"[TOOL FAILURE] Tool '{func_name}' failed: {tool_result.get('message', '')} (Args: {func_args})")
                            
                        tools_executed.append({
                            "name": func_name,
                            "arguments": func_args,
                            "result": tool_result
                        })
                        
                        # Add tool response to messages
                        tool_result_for_msg = {k: v for k, v in tool_result.items() if k != "__vision_urls__"} if isinstance(tool_result, dict) else tool_result
                        current_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{len(tools_executed)}"),
                            "name": func_name,
                            "content": json.dumps(tool_result_for_msg, ensure_ascii=False, default=str)
                        })
                        
                        # If the tool returned vision data, inject as a user message with image_url parts
                        vision_urls_from_tool = tool_result.get("__vision_urls__", []) if isinstance(tool_result, dict) else []
                        if vision_urls_from_tool:
                            vision_parts: List[Dict[str, Any]] = [
                                {"type": "text", "text": f"[Vision content loaded from message {tool_result.get('message_id', '?')}:]"}
                            ]
                            for v_url in vision_urls_from_tool:
                                vision_parts.append({"type": "image_url", "image_url": {"url": v_url}})
                            current_messages.append({"role": "user", "content": vision_parts})
                        
                    # Continue loop to get assistant's follow-up message after tool execution
                    continue
                else:
                    # Check [REFUSE]
                    clean_text = clean_response_text(content)
                    if not clean_text and content.strip():
                        clean_text = content.strip()
                        
                    if not clean_text and not tools_executed:
                        err_msg = "Endpoint returned empty response content."
                        logger.warning(f"Endpoint {endpoint.name} ({model_name}) error: {err_msg}")
                        return LLMExecutionResult(
                            text="",
                            error=err_msg,
                            model_used=model_name,
                            endpoint_id=endpoint.id
                        )
                        
                    refused = "[REFUSE]" in clean_text or clean_text.upper() == "[REFUSE]"
                    if refused:
                        clean_text = ""
                        
                    return LLMExecutionResult(
                        text=clean_text,
                        refused=refused,
                        tools_called=tools_executed,
                        model_used=model_name,
                        endpoint_id=endpoint.id,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens
                    )
            except Exception as e:
                logger.warning(f"Exception calling endpoint {endpoint.name}: {repr(e)}")
                return LLMExecutionResult(
                    text="",
                    error=repr(e),
                    model_used=model_name,
                    endpoint_id=endpoint.id
                )
                
    return LLMExecutionResult(
        text="",
        error="Max tool calling iterations exceeded",
        model_used=model_name,
        endpoint_id=endpoint.id
    )

async def _call_openai_compatible_stream(
    endpoint: EndpointModel,
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    context: Optional[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    request_id: str = "",
    max_iterations: int = 6
) -> LLMExecutionResult:
    """Streaming variant of _call_openai_compatible. Publishes events to event_bus."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url_path = "/responses" if getattr(endpoint, "endpoint_standard", "completions") == "responses" else "/chat/completions"
    url = f"{base_url}{url_path}"
    is_responses = getattr(endpoint, "endpoint_standard", "completions") == "responses"
    current_messages = list(messages)
    tools_executed: List[Dict[str, Any]] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    
    iteration = 0
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        while iteration < max_iterations:
            iteration += 1
            if is_responses:
                base_p = {
                    "model": model_name,
                    "messages": current_messages,
                    "stream": True,
                    "stream_options": {"include_usage": True}
                }
                if max_tokens is not None:
                    base_p["max_tokens"] = max_tokens
                if tools:
                    base_p["tools"] = tools
                payload = convert_to_responses_payload(base_p)
            else:
                payload = {
                    "model": model_name,
                    "messages": current_messages,
                    "temperature": 0.7,
                    "stream": True,
                    "stream_options": {"include_usage": True}
                }
                if max_tokens is not None:
                    payload["max_tokens"] = max_tokens
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                
            try:
                # Publish waiting_for_response event
                await event_bus.publish(StreamEvent(
                    "endpoint_waiting",
                    {
                        "endpoint_name": endpoint.name,
                        "model": model_name,
                        "provider": endpoint.provider,
                        "iteration": iteration,
                        "status": "waiting_for_response"
                    },
                    request_id
                ))

                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        err_body = await resp.aread()
                        err_msg = f"HTTP {resp.status_code}: {err_body.decode('utf-8', errors='replace')}"
                        logger.warning(f"Streaming endpoint {endpoint.name} ({model_name}) error: {err_msg}")
                        return LLMExecutionResult(
                            text="",
                            error=err_msg,
                            model_used=model_name,
                            endpoint_id=endpoint.id
                        )
                    
                    # Accumulators for the streamed response
                    accumulated_content = ""
                    accumulated_reasoning = ""
                    accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}  # index -> {id, name, arguments}
                    finish_reason = None
                    
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line == "data: [DONE]":
                            break
                        if not line.startswith("data: "):
                            continue
                            
                        try:
                            chunk_data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                            
                        # Extract usage if provided in stream chunks
                        usage = chunk_data.get("usage") or chunk_data.get("response", {}).get("usage")
                        if usage:
                            total_prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or total_prompt_tokens
                            total_completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or total_completion_tokens
                        
                        content_delta = None
                        reasoning_chunk = None
                        delta = {}
                        
                        choices = chunk_data.get("choices", [])
                        if choices and isinstance(choices, list) and len(choices) > 0:
                            delta = choices[0].get("delta", {})
                            chunk_finish = choices[0].get("finish_reason")
                            if chunk_finish:
                                finish_reason = chunk_finish
                            reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thought")
                            content_delta = delta.get("content")
                        else:
                            # Handle /v1/responses SSE stream formats
                            if chunk_data.get("type") == "error":
                                err_msg = chunk_data.get("error", {}).get("message", "Unknown error in stream")
                                raise Exception(f"Responses API error: {err_msg}")
                            elif "delta" in chunk_data:
                                d = chunk_data.get("delta")
                                if isinstance(d, str):
                                    content_delta = d
                                elif isinstance(d, dict):
                                    content_delta = d.get("text") or d.get("content")
                            elif "response" in chunk_data and isinstance(chunk_data["response"], dict):
                                resp_obj = chunk_data["response"]
                                if "output" in resp_obj and isinstance(resp_obj["output"], list):
                                    for item in resp_obj["output"]:
                                        if item.get("type") == "message":
                                            c = item.get("content", "")
                                            if isinstance(c, list):
                                                c = "".join([p.get("text","") for p in c if isinstance(p, dict)])
                                            
                                            if isinstance(c, str):
                                                if accumulated_content and c.startswith(accumulated_content):
                                                    content_delta = c[len(accumulated_content):]
                                                else:
                                                    content_delta = c
                            elif "output" in chunk_data and isinstance(chunk_data["output"], list):
                                for item in chunk_data["output"]:
                                    if item.get("type") == "message":
                                        c = item.get("content", "")
                                        if isinstance(c, list):
                                            c = "".join([p.get("text","") for p in c if isinstance(p, dict)])
                                        
                                        if isinstance(c, str):
                                            if accumulated_content and c.startswith(accumulated_content):
                                                content_delta = c[len(accumulated_content):]
                                            else:
                                                content_delta = c
                            
                            # Handle OpenWebUI function_call in streaming
                            if "item" in chunk_data and isinstance(chunk_data["item"], dict):
                                item = chunk_data["item"]
                                if item.get("type") in ("function_call", "tool_call"):
                                    idx = chunk_data.get("output_index", len(accumulated_tool_calls))
                                    if idx not in accumulated_tool_calls:
                                        accumulated_tool_calls[idx] = {
                                            "id": item.get("id", item.get("call_id", f"call_{idx}")),
                                            "name": "",
                                            "arguments": ""
                                        }
                                    tc_acc = accumulated_tool_calls[idx]
                                    if item.get("id"):
                                        tc_acc["id"] = item["id"]
                                    if item.get("name") and len(item["name"]) > len(tc_acc["name"]):
                                        tc_acc["name"] = item["name"]
                                    if item.get("arguments") and len(item["arguments"]) > len(tc_acc["arguments"]):
                                        tc_acc["arguments"] = item["arguments"]
                        if reasoning_chunk:
                            accumulated_reasoning += reasoning_chunk
                            await event_bus.publish(StreamEvent(
                                "reasoning_delta",
                                {"text": reasoning_chunk, "accumulated_length": len(accumulated_reasoning)},
                                request_id
                            ))

                        # Text content delta
                        if content_delta:
                            accumulated_content += content_delta
                            # Publish text delta event
                            await event_bus.publish(StreamEvent(
                                "text_delta",
                                {"text": content_delta, "accumulated_length": len(accumulated_content)},
                                request_id
                            ))
                        
                        # Tool call deltas
                        tc_deltas = delta.get("tool_calls", []) if 'delta' in locals() and isinstance(delta, dict) else []
                        for tc_delta in tc_deltas:
                            idx = tc_delta.get("index", 0)
                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": tc_delta.get("id", f"call_{idx}"),
                                    "name": "",
                                    "arguments": ""
                                }
                            tc_acc = accumulated_tool_calls[idx]
                            
                            if tc_delta.get("id"):
                                tc_acc["id"] = tc_delta["id"]
                            
                            func = tc_delta.get("function", {})
                            if func.get("name"):
                                tc_acc["name"] += func["name"]
                            if func.get("arguments"):
                                tc_acc["arguments"] += func["arguments"]
                    
                    # Check for tool calls (structured delta tool calls or inline XML tool calls during thinking)
                    tool_calls_list = []
                    if accumulated_tool_calls and tools:
                        for idx in sorted(accumulated_tool_calls.keys()):
                            tc = accumulated_tool_calls[idx]
                            tool_calls_list.append({
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"]
                                }
                            })
                    elif tools:
                        # Fallback for models emitting XML/JSON tool calls inside thinking/text
                        tool_calls_list = extract_text_tool_calls(accumulated_content, accumulated_reasoning)
                    
                    if tool_calls_list and tools:
                        # Append assistant message with tool calls
                        current_messages.append({
                            "role": "assistant",
                            "content": accumulated_content or None,
                            "tool_calls": tool_calls_list
                        })
                        
                        # Execute each tool call and publish events
                        for tc_obj in tool_calls_list:
                            func_name = tc_obj["function"]["name"]
                            func_args_str = tc_obj["function"]["arguments"]
                            try:
                                func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                            except Exception:
                                func_args = {}
                            
                            # Publish tool_call_start
                            await event_bus.publish(StreamEvent(
                                "tool_call_start",
                                {"tool_name": func_name, "arguments": func_args},
                                request_id
                            ))
                            
                            # Execute the tool
                            tool_result = await dispatch_tool_call(func_name, func_args, context)
                            is_err = isinstance(tool_result, dict) and (tool_result.get("status") == "error" or tool_result.get("error") is not None)
                            if is_err:
                                logger.warning(f"[TOOL FAILURE] Tool '{func_name}' failed: {tool_result.get('message', '')} (Args: {func_args})")
                                
                            tools_executed.append({
                                "name": func_name,
                                "arguments": func_args,
                                "result": tool_result
                            })
                            
                            # Publish tool_call_result
                            await event_bus.publish(StreamEvent(
                                "tool_call_result",
                                {
                                    "tool_name": func_name,
                                    "result": tool_result,
                                    "success": not is_err,
                                    "status": "error" if is_err else "success",
                                    "message": (tool_result.get("message") if is_err else "Executed successfully") if isinstance(tool_result, dict) else "Executed"
                                },
                                request_id
                            ))
                            
                            # Add tool response to messages
                            tool_result_for_msg = {k: v for k, v in tool_result.items() if k != "__vision_urls__"} if isinstance(tool_result, dict) else tool_result
                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tc_obj["id"],
                                "name": func_name,
                                "content": json.dumps(tool_result_for_msg, ensure_ascii=False, default=str)
                            })
                            
                            # If the tool returned vision data, inject as a user message with image_url parts
                            vision_urls_from_tool = tool_result.get("__vision_urls__", []) if isinstance(tool_result, dict) else []
                            if vision_urls_from_tool:
                                vision_parts: List[Dict[str, Any]] = [
                                    {"type": "text", "text": f"[Vision content loaded from message {tool_result.get('message_id', '?')}:]"}
                                ]
                                for v_url in vision_urls_from_tool:
                                    vision_parts.append({"type": "image_url", "image_url": {"url": v_url}})
                                current_messages.append({"role": "user", "content": vision_parts})
                            
                        # Continue the loop for the follow-up response
                        continue
                    else:
                        # Final text response
                        clean_text = clean_response_text(accumulated_content)
                        if not clean_text and accumulated_content.strip():
                            clean_text = accumulated_content.strip()

                        # If empty content was generated and no tools were called:
                        if not clean_text and not tools_executed:
                            err_msg = "Endpoint finished without producing final response content (likely truncated during reasoning or max_tokens reached)."
                            logger.warning(f"Endpoint {endpoint.name} ({model_name}) failed: {err_msg}")
                            return LLMExecutionResult(
                                text="",
                                error=err_msg,
                                model_used=model_name,
                                endpoint_id=endpoint.id
                            )

                        refused = "[REFUSE]" in clean_text or clean_text.upper() == "[REFUSE]"
                        if refused:
                            clean_text = ""
                            
                        return LLMExecutionResult(
                            text=clean_text,
                            refused=refused,
                            tools_called=tools_executed,
                            model_used=model_name,
                            endpoint_id=endpoint.id,
                            prompt_tokens=total_prompt_tokens,
                            completion_tokens=total_completion_tokens,
                            total_tokens=total_prompt_tokens + total_completion_tokens
                        )
            except Exception as e:
                logger.warning(f"Exception in streaming endpoint {endpoint.name}: {repr(e)}")
                return LLMExecutionResult(
                    text="",
                    error=repr(e),
                    model_used=model_name,
                    endpoint_id=endpoint.id
                )
                
    return LLMExecutionResult(
        text="",
        error="Max tool calling iterations exceeded (stream)",
        model_used=model_name,
        endpoint_id=endpoint.id
    )

async def _call_anthropic_native(
    endpoint: EndpointModel,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    context: Optional[Dict[str, Any]],
    max_tokens: Optional[int] = None
) -> LLMExecutionResult:
    """Native implementation for Anthropic Claude Messages API."""
    headers = {
        "x-api-key": endpoint.api_key or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    url = "https://api.anthropic.com/v1/messages"
    
    # Extract system prompt
    system_text = ""
    claude_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        else:
            claude_messages.append({"role": m["role"], "content": m["content"]})
            
    payload = {
        "model": endpoint.model_name,
        "system": system_text.strip(),
        "messages": claude_messages,
        "max_tokens": max_tokens if max_tokens is not None else 8192
    }
    
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                return LLMExecutionResult(text="", error=f"Anthropic HTTP {resp.status_code}: {resp.text}", model_used=endpoint.model_name)
            data = resp.json()
            usage = data.get("usage", {})
            p_tokens = usage.get("input_tokens", 0)
            c_tokens = usage.get("output_tokens", 0)
            content_blocks = data.get("content", [])
            text_result = "".join([b.get("text", "") for b in content_blocks if b.get("type") == "text"]).strip()
            refused = "[REFUSE]" in text_result
            if refused:
                text_result = ""
            return LLMExecutionResult(
                text=text_result,
                refused=refused,
                model_used=endpoint.model_name,
                endpoint_id=endpoint.id,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=p_tokens + c_tokens
            )
    except Exception as e:
        return LLMExecutionResult(text="", error=repr(e), model_used=endpoint.model_name, endpoint_id=endpoint.id)

async def execute_llm_with_fallback(
    endpoint_ids: List[str],
    messages: List[Dict[str, Any]],
    enable_tools: bool = True,
    context: Optional[Dict[str, Any]] = None,
    max_tokens: Optional[int] = None,
    stream: bool = False,
    request_id: Optional[str] = None
) -> LLMExecutionResult:
    """Executes the request iterating through the priority chain (Bot Chain + Global Fallbacks)."""
    # Generate request_id if streaming and not provided
    if stream and not request_id:
        request_id = event_bus.new_request_id()

    # 1. Retrieve bot endpoints ordered by priority
    bot_endpoints = await get_endpoints_by_ids(endpoint_ids)
    
    # 2. Retrieve global fallbacks and settings
    async with AsyncSessionLocal() as session:
        # Retrieve fallbacks
        stmt_fb = select(EndpointModel).where(EndpointModel.is_global_fallback == True)
        res_fb = await session.execute(stmt_fb)
        global_fallbacks = list(res_fb.scalars().all())
        
        # Retrieve global settings for max_tool_iterations
        stmt_set = select(GlobalSettingsModel).where(GlobalSettingsModel.id == "default")
        res_set = await session.execute(stmt_set)
        settings = res_set.scalar_one_or_none()
        global_max_iterations = settings.max_tool_iterations if settings else 6
        
    if context is None:
        context = {}
    context["max_iterations"] = global_max_iterations
    
    # Build complete chain avoiding duplicates
    seen_ids = set()
    chain: List[EndpointModel] = []
    for ep in bot_endpoints:
        if ep.id not in seen_ids:
            chain.append(ep)
            seen_ids.add(ep.id)
    for ep in global_fallbacks:
        if ep.id not in seen_ids:
            chain.append(ep)
            seen_ids.add(ep.id)
            
    if not chain:
        return LLMExecutionResult(
            text="No LLM endpoint configured or available in fallback chain.",
            error="No endpoints available in fallback chain."
        )
        
    last_error = None
    for index, endpoint in enumerate(chain):
        logger.info(f"Attempting LLM endpoint #{index+1}/{len(chain)}: {endpoint.name} ({endpoint.provider} - {endpoint.model_name})")
        
        # Publish endpoint attempt event
        if stream and request_id:
            await event_bus.publish(StreamEvent(
                "endpoint_attempt",
                {
                    "endpoint_name": endpoint.name,
                    "model": endpoint.model_name,
                    "provider": endpoint.provider,
                    "chain_index": index + 1,
                    "chain_total": len(chain)
                },
                request_id
            ))

        res = await _call_single_endpoint(
            endpoint=endpoint,
            messages=messages,
            enable_tools=enable_tools,
            tools=AVAILABLE_TOOLS if enable_tools else None,
            context=context,
            max_tokens=max_tokens,
            stream=stream,
            request_id=request_id
        )
        
        if not res.error:
            # Successful call
            return res
        else:
            logger.warning(f"Failover triggered: Endpoint {endpoint.name} failed ({res.error}). Proceeding to next...")
            last_error = res.error
            if stream and request_id and (index + 1 < len(chain)):
                next_ep = chain[index + 1]
                await event_bus.publish(StreamEvent(
                    "endpoint_failover",
                    {
                        "failed_endpoint": endpoint.name,
                        "failed_model": endpoint.model_name,
                        "error": str(res.error),
                        "next_endpoint": next_ep.name,
                        "next_model": next_ep.model_name,
                        "next_index": index + 2,
                        "chain_total": len(chain)
                    },
                    request_id
                ))
            
    return LLMExecutionResult(
        text=f"All endpoints in fallback chain failed. Last error: {last_error}",
        error=f"All {len(chain)} endpoints in fallback chain failed: {last_error}"
    )
