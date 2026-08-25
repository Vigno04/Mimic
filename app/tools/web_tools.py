import asyncio
from typing import Dict, Any, List
import httpx
from sqlalchemy import select
from app.database.session import AsyncSessionLocal
from app.database.models import GlobalSettingsModel

async def exec_web_search(query: str, num_results: int = 5) -> Dict[str, Any]:
    """Searches the web for up-to-date information."""
    # Fetch settings
    provider = "duckduckgo"
    searxng_url = ""
    max_results = 5
    safesearch = "moderate"
    
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(GlobalSettingsModel).where(GlobalSettingsModel.id == "default")
            result = await session.execute(stmt)
            settings = result.scalar_one_or_none()
            if settings:
                provider = settings.web_search_provider
                searxng_url = settings.searxng_url
                max_results = settings.max_search_results
                safesearch = settings.search_safesearch
    except Exception as e:
        import logging
        logging.getLogger("mimic").warning(f"Failed to fetch global settings in web_tools: {e}")

    num_results = min(max(1, num_results), max_results)
    
    if provider == "searxng" and searxng_url:
        try:
            url = searxng_url.rstrip("/") + "/search"
            # SearXNG safesearch: 0=none, 1=moderate, 2=strict
            ss_map = {"off": "0", "moderate": "1", "strict": "2"}
            ss_val = ss_map.get(safesearch, "1")
            
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    params={"q": query, "format": "json", "safesearch": ss_val}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    formatted = [
                        {"title": r.get("title", ""), "href": r.get("url", ""), "body": r.get("content", "")}
                        for r in results[:num_results]
                    ]
                    return {"status": "success", "query": query, "results": formatted}
                else:
                    # Fallback on failure if searxng fails HTTP
                    pass
        except Exception as e:
            # Fallback on exception
            pass
            
    # DuckDuckGo fallback / default
    ddg_ss_map = {"off": "off", "moderate": "moderate", "strict": "strict"}
    ddg_ss_val = ddg_ss_map.get(safesearch, "moderate")
    
    try:
        from duckduckgo_search import DDGS
        def _ddg_sync():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=num_results, safesearch=ddg_ss_val))
                
        results = await asyncio.to_thread(_ddg_sync)
        if results:
            formatted = [
                {"title": r.get("title", ""), "href": r.get("href", ""), "body": r.get("body", "")}
                for r in results
            ]
            return {"status": "success", "query": query, "results": formatted}
    except Exception as e:
        # Fallback to DuckDuckGo Instant Answer API / HTML
        pass

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
            )
            if resp.status_code == 200:
                data = resp.json()
                abstract = data.get("AbstractText", "")
                heading = data.get("Heading", "")
                related = [t.get("Text", "") for t in data.get("RelatedTopics", []) if isinstance(t, dict) and "Text" in t]
                return {
                    "status": "success",
                    "query": query,
                    "results": [
                        {"title": heading or query, "body": abstract or ("; ".join(related[:3]) if related else "No specific results found.")}
                    ]
                }
    except Exception as e:
        return {"status": "error", "query": query, "message": f"Error during web search: {str(e)}", "results": []}

    return {"status": "success", "query": query, "results": []}
