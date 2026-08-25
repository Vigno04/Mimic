from typing import Dict, Any, List, Optional
import json
from app.tools.memory_tools import (
    exec_save_user_memory,
    exec_remove_user_memory,
    exec_get_user_profile,
    exec_save_server_memory,
    exec_remove_server_memory,
    exec_get_server_memories,
    exec_update_user_memory,
    exec_update_server_memory
)
from app.tools.search_tools import exec_search_chat_history
from app.tools.web_tools import exec_web_search
from app.tools.discord_tools import (
    exec_add_reaction,
    exec_send_bot_command,
    exec_get_server_info,
    exec_get_channel_info,
    exec_get_user_info,
    exec_list_channel_members,
    exec_send_message_to_channel
)

AVAILABLE_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_reaction",
            "description": "Adds an emoji reaction to the current message (or a specified message ID). Use this to express emotion, acknowledge messages, or complement your response naturally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "emoji": {
                        "type": "string",
                        "description": "The Unicode or custom emoji to add as a reaction (e.g. '👍', '🔥', '😂', '❤️', '👀')."
                    },
                    "message_id": {
                        "type": "string",
                        "description": "Optional Discord message ID. If omitted, reacts to the current message."
                    }
                },
                "required": ["emoji"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_user_memory",
            "description": "Saves a new persistent memory about a user. IMPORTANT: Before calling this, check '# MEMORIES ABOUT ACTIVE USERS' or call 'get_user_profile' first to verify if this user fact already exists. If an existing memory on this topic is found, call 'update_user_memory' instead to avoid duplicates!",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id_or_name": {
                        "type": "string",
                        "description": "The numeric ID (e.g. '123456789') or username of the user."
                    },
                    "fact": {
                        "type": "string",
                        "description": "The clear personal fact or preference to remember (e.g. 'His name is Alex and he works as a Python developer')."
                    },
                    "category": {
                        "type": "string",
                        "description": "Category of the memory (e.g. 'profile', 'preferences', 'hobbies', 'work', 'timezone').",
                        "default": "general"
                    }
                },
                "required": ["user_id_or_name", "fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_user_memory",
            "description": "Deletes an obsolete, retracted, or incorrect personal memory about a user using its ID or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id_or_fact_key": {
                        "type": "string",
                        "description": "The unique memory ID (shown in '# MEMORIES ABOUT ACTIVE USERS') or keyword of the memory to delete."
                    }
                },
                "required": ["memory_id_or_fact_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": "Retrieves all saved memories and profile facts for a specific user. Call this before saving a new user memory if the user's memories are not in your current context, to prevent duplicate entries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id_or_name": {
                        "type": "string",
                        "description": "Username, mention (<@ID>), or numeric ID of the user."
                    }
                },
                "required": ["user_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_server_memory",
            "description": "Saves a new global server fact, ongoing event, or server lore. IMPORTANT: Check '# SERVER LORE AND MEMORIES' or call 'get_server_memories' first. If a matching lore entry exists, use 'update_server_memory' instead of creating a duplicate!",
            "parameters": {
                "type": "object",
                "properties": {
                    "key_phrase": {
                        "type": "string",
                        "description": "Short keyword or topic title (e.g. 'chess_tournament', 'spoiler_policy', 'minecraft_server')."
                    },
                    "fact": {
                        "type": "string",
                        "description": "Detailed description of the server event, rule, or global fact."
                    },
                    "category": {
                        "type": "string",
                        "description": "Category (e.g. 'events', 'lore', 'rules', 'server').",
                        "default": "general"
                    }
                },
                "required": ["key_phrase", "fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_server_memory",
            "description": "Deletes an outdated, ended, or concluded server lore / event entry by ID or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id_or_keyword": {
                        "type": "string",
                        "description": "The unique memory ID (shown in '# SERVER LORE AND MEMORIES') or keyword of the server memory to remove."
                    }
                },
                "required": ["memory_id_or_keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_server_memories",
            "description": "Retrieves recorded server facts, events, or global lore. Use this before saving server memories to check for existing facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category_or_search": {
                        "type": "string",
                        "description": "Optional category filter or search keyword."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_server_memory",
            "description": "Updates or edits an existing global server memory using its unique memory ID. Use this to modify existing server lore without creating duplicate entries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The unique ID of the server memory to update (as listed in '# SERVER LORE AND MEMORIES')."
                    },
                    "new_fact": {
                        "type": "string",
                        "description": "The updated fact content. Can include user mentions (<@ID>) or message references (msg:ID)."
                    }
                },
                "required": ["memory_id", "new_fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "Updates or edits an existing personal user memory using its unique memory ID. Use this whenever updating a user's details to prevent duplicate entries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The unique ID of the user memory to update (as listed in '# MEMORIES ABOUT ACTIVE USERS')."
                    },
                    "new_fact": {
                        "type": "string",
                        "description": "The updated fact content."
                    }
                },
                "required": ["memory_id", "new_fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_chat_history",
            "description": "Searches stored chat messages across the server using full-text search (SQLite FTS5). Use this whenever a user asks 'what did we talk about earlier?', 'who said X?', 'did anyone mention Y?', or references previous discussions outside the immediate chat window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The phrase or search terms to look for in past messages."
                    },
                    "channel_name": {
                        "type": "string",
                        "description": "Optional channel name to narrow search."
                    },
                    "author": {
                        "type": "string",
                        "description": "Optional author username."
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Filter start date (ISO format YYYY-MM-DD)."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Filter end date (ISO format YYYY-MM-DD)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of messages to return (default 15).",
                        "default": 15
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Searches the live web for current information, news, real-time facts, documentation, or events beyond your knowledge cutoff. Use this whenever the user asks about current events, real-time data, weather, or topics requiring up-to-date sources. NEVER guess when you can search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Targeted and concise web search query."
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of desired search results (1-8, default 5).",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_bot_command",
            "description": "Sends a prefix command to other bots in a Discord channel (e.g. '!play <song>', '-skip', '?help').",
            "parameters": {
                "type": "object",
                "properties": {
                    "command_string": {
                        "type": "string",
                        "description": "The full command string to send (e.g. '!play bohemian rhapsody')."
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "The numeric channel ID, mention (<#ID>), or exact channel name. If omitted, uses the current channel."
                    }
                },
                "required": ["command_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_channel",
            "description": "Sends a text message to a specific Discord channel in the server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_text": {
                        "type": "string",
                        "description": "The text content of the message to send."
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "The numeric channel ID (e.g. '123456789'), mention (<#123456789>), or exact channel name (e.g. 'general')."
                    }
                },
                "required": ["message_text", "channel_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_server_info",
            "description": "Retrieves server details, member count, role statistics, and the full list of available text channels with their numeric IDs.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_channel_info",
            "description": "Retrieves details about a specific channel (topic, category, NSFW status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "The numeric ID, mention, or exact channel name. If omitted, uses the current channel."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_info",
            "description": "Retrieves details about a user in the server (join date, assigned roles) by user ID or username.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "The numeric ID of the user, mention (<@ID>), or username."
                    }
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_channel_members",
            "description": "Lists active members who have access to a specific channel along with their numeric Discord IDs (up to 50 members). Use this to find user IDs for mentions (<@ID>).",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "Optional numeric channel ID, mention, or exact channel name. If omitted, lists members of the current channel."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of members to list (max 50, default 50).",
                        "default": 50
                    }
                }
            }
        }
    }
]

async def dispatch_tool_call(name: str, arguments: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Executes the tool requested by the LLM with the provided arguments."""
    ctx = context or {}
    current_user_id = ctx.get("user_id")
    guild_id = ctx.get("guild_id")
    bot_id = ctx.get("bot_id")
    
    try:
        if name == "add_reaction":
            return await exec_add_reaction(
                emoji=arguments.get("emoji", "👍"),
                message_id=arguments.get("message_id"),
                discord_context=ctx
            )
        elif name == "save_user_memory":
            return await exec_save_user_memory(
                user_id_or_name=arguments.get("user_id_or_name", ctx.get("user_name", "unknown")),
                fact=arguments.get("fact", ""),
                category=arguments.get("category", "general"),
                current_user_id=current_user_id,
                current_user_name=ctx.get("user_name"),
                bot_id=bot_id,
                discord_context=ctx
            )
        elif name == "remove_user_memory":
            return await exec_remove_user_memory(
                memory_id_or_fact_key=arguments.get("memory_id_or_fact_key", ""),
                user_id=current_user_id,
                bot_id=bot_id
            )
        elif name == "get_user_profile":
            return await exec_get_user_profile(
                user_id_or_name=arguments.get("user_id_or_name", ctx.get("user_name", "")),
                bot_id=bot_id
            )
        elif name == "save_server_memory":
            return await exec_save_server_memory(
                key_phrase=arguments.get("key_phrase", ""),
                fact=arguments.get("fact", ""),
                category=arguments.get("category", "general"),
                guild_id=guild_id,
                bot_id=bot_id
            )
        elif name == "remove_server_memory":
            return await exec_remove_server_memory(
                memory_id_or_keyword=arguments.get("memory_id_or_keyword", ""),
                bot_id=bot_id
            )
        elif name == "get_server_memories":
            return await exec_get_server_memories(
                category_or_search=arguments.get("category_or_search"),
                guild_id=guild_id,
                bot_id=bot_id
            )
        elif name == "update_server_memory":
            return await exec_update_server_memory(
                memory_id=arguments.get("memory_id", ""),
                new_fact=arguments.get("new_fact", ""),
                bot_id=bot_id
            )
        elif name == "update_user_memory":
            return await exec_update_user_memory(
                memory_id=arguments.get("memory_id", ""),
                new_fact=arguments.get("new_fact", ""),
                bot_id=bot_id
            )
        elif name == "search_chat_history":
            return await exec_search_chat_history(
                query=arguments.get("query", ""),
                channel_name=arguments.get("channel_name"),
                channel_id=ctx.get("channel_id"),
                author=arguments.get("author"),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                limit=int(arguments.get("limit", 15))
            )
        elif name == "web_search":
            return await exec_web_search(
                query=arguments.get("query", ""),
                num_results=int(arguments.get("num_results", 5))
            )
        elif name == "send_bot_command":
            return await exec_send_bot_command(
                command_string=arguments.get("command_string", ""),
                channel_id=arguments.get("channel_id") or ctx.get("channel_id"),
                discord_context=ctx
            )
        elif name == "send_message_to_channel":
            return await exec_send_message_to_channel(
                message_text=arguments.get("message_text", ""),
                channel_id=arguments.get("channel_id", ""),
                discord_context=ctx
            )
        elif name == "get_server_info":
            return await exec_get_server_info(
                discord_context=ctx
            )
        elif name == "get_channel_info":
            return await exec_get_channel_info(
                channel_id=arguments.get("channel_id"),
                discord_context=ctx
            )
        elif name == "get_user_info":
            return await exec_get_user_info(
                user_id=arguments.get("user_id", ""),
                discord_context=ctx
            )
        elif name == "list_channel_members":
            return await exec_list_channel_members(
                channel_id=arguments.get("channel_id"),
                limit=int(arguments.get("limit", 50)),
                discord_context=ctx
            )
        else:
            return {"status": "error", "message": f"Unknown tool '{name}'."}
    except Exception as e:
        return {"status": "error", "message": f"Error executing tool {name}: {str(e)}"}
