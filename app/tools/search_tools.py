from typing import Dict, Any, Optional
from app.database.queries import search_messages_fts

async def exec_search_chat_history(
    query: str,
    channel_name: Optional[str] = None,
    channel_id: Optional[str] = None,
    author: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 15
) -> Dict[str, Any]:
    """Searches stored chat messages using SQLite FTS5 full-text search."""
    results = await search_messages_fts(
        query=query,
        channel_id=channel_id,
        channel_name=channel_name,
        author_name=author,
        start_date=start_date,
        end_date=end_date,
        limit=min(limit, 30)
    )
    return {
        "status": "success",
        "query": query,
        "results_count": len(results),
        "messages": results
    }
