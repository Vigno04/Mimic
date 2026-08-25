import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete, update
from app.database.models import EndpointModel
from app.database.session import get_db, AsyncSession
from app.core.llm_client import test_endpoint_connectivity

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])

class EndpointCreate(BaseModel):
    name: str
    provider: str  # openai, gemini, anthropic, ollama, custom
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: str
    is_global_fallback: bool = False

class EndpointUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    is_global_fallback: Optional[bool] = None

@router.get("")
@router.get("/")
async def list_endpoints(db: AsyncSession = Depends(get_db)):
    stmt = select(EndpointModel).order_by(EndpointModel.name)
    res = await db.execute(stmt)
    return [e.to_dict() for e in res.scalars().all()]

@router.post("")
@router.post("/")
async def create_endpoint(payload: EndpointCreate, db: AsyncSession = Depends(get_db)):
    endpoint = EndpointModel(
        id=str(uuid.uuid4()),
        name=payload.name,
        provider=payload.provider.lower(),
        base_url=payload.base_url,
        api_key=payload.api_key,
        model_name=payload.model_name,
        is_global_fallback=payload.is_global_fallback
    )
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint.to_dict()

@router.put("/{endpoint_id}")
async def update_endpoint(endpoint_id: str, payload: EndpointUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(EndpointModel).where(EndpointModel.id == endpoint_id)
    res = await db.execute(stmt)
    endpoint = res.scalars().first()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
        
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(endpoint, key, value)
        
    await db.commit()
    await db.refresh(endpoint)
    return endpoint.to_dict()

@router.delete("/{endpoint_id}")
async def delete_endpoint(endpoint_id: str, db: AsyncSession = Depends(get_db)):
    stmt = delete(EndpointModel).where(EndpointModel.id == endpoint_id)
    res = await db.execute(stmt)
    await db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
    return {"status": "success", "message": "Endpoint deleted successfully."}

@router.post("/{endpoint_id}/test")
async def test_endpoint(endpoint_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(EndpointModel).where(EndpointModel.id == endpoint_id)
    res = await db.execute(stmt)
    endpoint = res.scalars().first()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
    return await test_endpoint_connectivity(endpoint)
