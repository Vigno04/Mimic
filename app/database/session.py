import os
from pathlib import Path
import aiosqlite
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings
from app.database.models import Base

def get_db_file_path() -> Path:
    """Returns the Path to the SQLite database file based on DATABASE_URL."""
    db_url = settings.DATABASE_URL
    if "///" in db_url:
        path_str = db_url.split("///", 1)[1]
        return Path(path_str)
    return settings.DATA_DIR / "mimic.db"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def init_db():
    db_path = get_db_file_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Enable SQLite WAL mode, busy_timeout, and synchronous settings for high concurrency
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 10000;")
        await db.execute("PRAGMA synchronous = NORMAL;")
        await db.commit()

    # 2. Create standard SQLAlchemy tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Auto-migrate audit_logs table (add new columns if missing)
    async with aiosqlite.connect(db_path) as db:
        for col in ["input_text", "output_text", "error_message", "system_prompt"]:
            try:
                await db.execute(f"ALTER TABLE audit_logs ADD COLUMN {col} TEXT;")
            except Exception:
                pass # Column already exists

        
    # 3. Setup SQLite FTS5 Virtual Table & Triggers for chat_messages
    async with aiosqlite.connect(db_path) as db:
        # Create FTS5 virtual table
        await db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
                id UNINDEXED,
                channel_id UNINDEXED,
                channel_name,
                author_id UNINDEXED,
                author_name,
                content,
                timestamp UNINDEXED
            );
        """)
        
        # Trigger on INSERT into chat_messages
        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_chat_messages_ai AFTER INSERT ON chat_messages BEGIN
                INSERT INTO chat_messages_fts(id, channel_id, channel_name, author_id, author_name, content, timestamp)
                VALUES (new.id, new.channel_id, new.channel_name, new.author_id, new.author_name, new.content, new.timestamp);
            END;
        """)
        
        # Trigger on DELETE from chat_messages
        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_chat_messages_ad AFTER DELETE ON chat_messages BEGIN
                DELETE FROM chat_messages_fts WHERE id = old.id;
            END;
        """)
        
        # Trigger on UPDATE from chat_messages
        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_chat_messages_au AFTER UPDATE ON chat_messages BEGIN
                DELETE FROM chat_messages_fts WHERE id = old.id;
                INSERT INTO chat_messages_fts(id, channel_id, channel_name, author_id, author_name, content, timestamp)
                VALUES (new.id, new.channel_id, new.channel_name, new.author_id, new.author_name, new.content, new.timestamp);
            END;
        """)
        
        # Auto-migration: ensure bot_id and triggers columns exist
        for tbl in ["server_memories", "user_memories"]:
            try:
                cursor = await db.execute(f"PRAGMA table_info({tbl})")
                cols = [row[1] for row in await cursor.fetchall()]
                if "bot_id" not in cols:
                    await db.execute(f"ALTER TABLE {tbl} ADD COLUMN bot_id TEXT;")
            except Exception:
                pass

        try:
            cursor = await db.execute("PRAGMA table_info(bots)")
            cols = [row[1] for row in await cursor.fetchall()]
            if "triggers" not in cols:
                await db.execute("ALTER TABLE bots ADD COLUMN triggers TEXT;")
        except Exception:
            pass
            
        # Ensure default global settings exist
        try:
            await db.execute("""
                INSERT OR IGNORE INTO global_settings (id, web_search_provider, searxng_url, max_search_results, search_safesearch)
                VALUES ('default', 'duckduckgo', '', 5, 'moderate');
            """)
        except Exception as e:
            import logging
            logging.getLogger("mimic").warning(f"Failed to init global settings: {e}")

        # Auto-backfill discord_users from user_memories and chat_messages if empty
        try:
            await db.execute("""
                INSERT OR IGNORE INTO discord_users (id, username, global_name, display_name, is_bot, first_seen, last_seen, updated_at)
                SELECT DISTINCT user_id, username, username, username, 0, created_at, updated_at, updated_at
                FROM user_memories
                WHERE user_id IS NOT NULL AND user_id != '';
            """)
            await db.execute("""
                INSERT OR IGNORE INTO discord_users (id, username, global_name, display_name, is_bot, first_seen, last_seen, updated_at)
                SELECT DISTINCT author_id, author_name, author_name, author_name, 0, timestamp, timestamp, timestamp
                FROM chat_messages
                WHERE author_id IS NOT NULL AND author_id != '';
            """)
        except Exception as e:
            import logging
            logging.getLogger("mimic").warning(f"Notice on discord_users backfill: {e}")

        await db.commit()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
