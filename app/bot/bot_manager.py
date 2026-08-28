import asyncio
import logging
import discord
from typing import Dict, Any, Optional
from sqlalchemy import select, update
from app.database.models import BotModel
from app.database.session import AsyncSessionLocal
from app.bot.client import DiscordBotInstance

logger = logging.getLogger(__name__)

class MultiBotManager:
    def __init__(self):
        self.active_instances: Dict[str, DiscordBotInstance] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    async def start_bot(self, bot_id: str) -> Dict[str, Any]:
        """Starts a Discord bot given its bot_id."""
        if bot_id in self.active_instances and self.active_instances[bot_id].status == "running":
            return {"status": "already_running", "bot_id": bot_id}

        async with AsyncSessionLocal() as session:
            stmt = select(BotModel).where(BotModel.id == bot_id)
            res = await session.execute(stmt)
            bot_model = res.scalars().first()
            if not bot_model:
                return {"status": "error", "message": f"Bot {bot_id} not found in database."}
                
            config = bot_model.to_dict()
            token = config.get("discord_token", "").strip().strip('"\'')
            if token.lower().startswith("bot "):
                token = token[4:].strip()
            if not token:
                return {"status": "error", "message": "Discord token not configured."}

        # Stop existing instance if any
        await self.stop_bot(bot_id)

        instance = DiscordBotInstance(config)
        self.active_instances[bot_id] = instance
        instance.status = "starting"

        async def _run_bot():
            try:
                await instance.bot_client.start(token)
            except asyncio.CancelledError:
                logger.info(f"Bot task {bot_id} cancelled.")
            except discord.errors.PrivilegedIntentsRequired as e:
                logger.error(f"Privileged Intents error for bot {bot_id}: {e}")
                instance.status = "error"
                instance.last_error = "Privileged Intents Error: Enable 'MESSAGE CONTENT INTENT' in Discord Developer Portal (Bot tab > Privileged Gateway Intents)."
            except discord.errors.LoginFailure as e:
                logger.error(f"Login failure for bot {bot_id}: {e}")
                instance.status = "error"
                instance.last_error = "Invalid Discord Token: Verify your Bot Token in the Discord Developer Portal."
            except Exception as e:
                logger.error(f"Runtime error for bot {bot_id}: {e}")
                instance.status = "error"
                instance.last_error = str(e)
            finally:
                if not instance.bot_client.is_closed():
                    await instance.bot_client.close()
                
                # Update DB status ONLY if it stopped due to an error.
                # If stopped normally or cancelled by stop_bot, stop_bot handles DB if needed.
                if instance.status == "error":
                    async with AsyncSessionLocal() as session:
                        await session.execute(
                            update(BotModel).where(BotModel.id == bot_id).values(is_running=False)
                        )
                        await session.commit()
                else:
                    instance.status = "stopped"

        # Update DB status to running
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(BotModel).where(BotModel.id == bot_id).values(is_running=True)
            )
            await session.commit()

        task = asyncio.create_task(_run_bot())
        self.tasks[bot_id] = task
        return {"status": "started", "bot_id": bot_id}

    async def stop_bot(self, bot_id: str, update_db: bool = True) -> Dict[str, Any]:
        """Stops a running Discord bot."""
        instance = self.active_instances.get(bot_id)
        task = self.tasks.get(bot_id)

        if instance:
            instance.status = "stopping"
            try:
                if not instance.bot_client.is_closed():
                    await instance.bot_client.close()
            except Exception as e:
                logger.warning(f"Error closing client for bot {bot_id}: {e}")

        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if bot_id in self.active_instances:
            del self.active_instances[bot_id]
        if bot_id in self.tasks:
            del self.tasks[bot_id]

        # Update DB only if requested
        if update_db:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(BotModel).where(BotModel.id == bot_id).values(is_running=False)
                )
                await session.commit()

        return {"status": "stopped", "bot_id": bot_id}

    async def restart_bot(self, bot_id: str) -> Dict[str, Any]:
        """Restarts a Discord bot."""
        await self.stop_bot(bot_id)
        await asyncio.sleep(1.0)
        return await self.start_bot(bot_id)

    def get_status(self, bot_id: str) -> Dict[str, Any]:
        """Returns live bot status."""
        instance = self.active_instances.get(bot_id)
        if not instance:
            return {"status": "stopped", "online": False}
            
        # Calculate time to next spontaneous message
        import time
        next_auto_msg = None
        spontaneous_rules = [r for r in instance.config.get("triggers", []) if r.get("type") == "spontaneous"]
        if spontaneous_rules:
            now = time.time()
            next_times = []
            for rule in spontaneous_rules:
                pattern_str = rule.get("pattern", "1/day")
                rule_key = f"{pattern_str}_{rule.get('channel_id', '')}_{rule.get('topic', '')}"
                
                last_run = instance.spontaneous_last_run.get(rule_key)
                interval = instance.spontaneous_intervals.get(rule_key)
                
                if last_run is not None and interval is not None:
                    next_times.append(last_run + interval)
            
            if next_times:
                closest = min(next_times)
                diff = closest - now
                if diff <= 0:
                    next_auto_msg = "Imminent"
                else:
                    hours, rem = divmod(diff, 3600)
                    minutes, _ = divmod(rem, 60)
                    next_auto_msg = f"{int(hours)}h {int(minutes)}m"
            else:
                next_auto_msg = "Initializing..."
                
        return {
            "status": instance.status,
            "online": instance.status == "running",
            "last_error": instance.last_error,
            "user": str(instance.bot_client.user) if instance.bot_client.user else None,
            "next_auto_msg": next_auto_msg
        }

    async def start_all_enabled_bots(self):
        """Automatically starts all bots marked as is_running in DB on application startup."""
        async with AsyncSessionLocal() as session:
            stmt = select(BotModel).where(BotModel.is_running == True)
            res = await session.execute(stmt)
            bots_to_start = res.scalars().all()
            for b in bots_to_start:
                logger.info(f"Auto-starting bot configured as active: {b.name} (ID: {b.id})")
                await self.start_bot(b.id)

    async def stop_all_bots(self, update_db: bool = True):
        """Stops all active bots cleanly."""
        active_ids = list(self.active_instances.keys())
        for bot_id in active_ids:
            await self.stop_bot(bot_id, update_db=update_db)

bot_manager = MultiBotManager()
