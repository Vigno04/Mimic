from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.session import get_db
from app.database.models import GlobalSettingsModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdate(BaseModel):
    web_search_provider: str
    searxng_url: str
    max_search_results: int
    search_safesearch: str
    max_tool_iterations: int

@router.get("")
@router.get("/")
async def get_settings(db: AsyncSession = Depends(get_db)):
    stmt = select(GlobalSettingsModel).where(GlobalSettingsModel.id == "default")
    result = await db.execute(stmt)
    settings = result.scalar_one_or_none()
    
    if not settings:
        # Create default if missing somehow
        settings = GlobalSettingsModel(id="default")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        
    return settings.to_dict()

@router.post("")
@router.post("/")
async def update_settings(update_data: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(GlobalSettingsModel).where(GlobalSettingsModel.id == "default")
    result = await db.execute(stmt)
    settings = result.scalar_one_or_none()
    
    if not settings:
        settings = GlobalSettingsModel(id="default")
        db.add(settings)
        
    settings.web_search_provider = update_data.web_search_provider
    settings.searxng_url = update_data.searxng_url
    settings.max_search_results = update_data.max_search_results
    settings.search_safesearch = update_data.search_safesearch
    settings.max_tool_iterations = update_data.max_tool_iterations
    
    await db.commit()
    await db.refresh(settings)
    
    return {"status": "success", "settings": settings.to_dict()}
