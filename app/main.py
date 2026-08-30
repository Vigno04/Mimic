import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.database.session import init_db
from app.database.queries import migrate_db
from app.bot.bot_manager import bot_manager
from app.api.endpoints_router import router as endpoints_router
from app.api.bots_router import router as bots_router
from app.api.memories_router import router as memories_router
from app.api.stats_router import router as stats_router
from app.api.playground_router import router as playground_router
from app.api.backup_router import router as backup_router
from app.api.logs_router import router as logs_router
from app.api.chat_router import router as chat_router
from app.api.settings_router import router as settings_router

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mimic")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Mimic...")
    # 1. Initialize SQLite Database + FTS5
    await init_db()
    await migrate_db()
    logger.info("Database and FTS5 indices initialized.")
    
    # 2. Start bots configured as active
    try:
        await bot_manager.start_all_enabled_bots()
    except Exception as e:
        logger.warning(f"Auto-starting active bots error: {e}")
        
    yield
    
    # 3. Shutdown
    logger.info("Shutting down...")
    await bot_manager.stop_all_bots(update_db=False)
    logger.info("All bots have been stopped.")

app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static & Templates
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web" / "static"
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Include API Routers
app.include_router(endpoints_router)
app.include_router(bots_router)
app.include_router(memories_router)
app.include_router(stats_router)
app.include_router(playground_router)
app.include_router(backup_router)
app.include_router(logs_router)
app.include_router(chat_router)
app.include_router(settings_router)

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": settings.APP_NAME}

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#5865F2"/>
  <path d="M70.5 31.5c-4.8-2.2-9.9-3.8-15.3-4.6-.2.4-.4.8-.6 1.2-5.7-.9-11.5-.9-17.2 0-.2-.4-.4-.8-.6-1.2-5.4.8-10.5 2.4-15.3 4.6-9.7 14.5-12.4 28.7-11 42.6 6.4 4.7 12.6 7.6 18.7 9.5 1.5-2.1 2.9-4.3 4-6.7-2.2-.8-4.3-1.9-6.3-3.1.5-.4 1-.8 1.5-1.1 12.3 5.7 25.6 5.7 37.7 0 .5.4 1 .7 1.5 1.1-2 1.3-4.1 2.3-6.3 3.1 1.2 2.4 2.5 4.6 4 6.7 6.1-1.9 12.3-4.8 18.7-9.5 1.7-16-2.5-30.1-11-42.6zM37.5 59.5c-3.6 0-6.5-3.3-6.5-7.3s2.9-7.3 6.5-7.3c3.6 0 6.5 3.3 6.5 7.3s-2.9 7.3-6.5 7.3zm25 0c-3.6 0-6.5-3.3-6.5-7.3s2.9-7.3 6.5-7.3c3.6 0 6.5 3.3 6.5 7.3s-2.9 7.3-6.5 7.3z" fill="#FFFFFF"/>
</svg>"""

@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    from fastapi.responses import Response
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "app_name": settings.APP_NAME}
    )
