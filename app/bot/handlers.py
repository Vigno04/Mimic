import discord
from discord import app_commands
from typing import TYPE_CHECKING
from app.database.queries import wipe_user_memories

if TYPE_CHECKING:
    from app.bot.client import DiscordBotInstance

def setup_slash_commands(bot_instance: "DiscordBotInstance"):
    """Registers standard slash commands for the bot."""
    bot = bot_instance.bot_client
    
    @bot.tree.command(name="forgetme", description="Deletes all memories stored about you from this bot's database.")
    async def cmd_forgetme(interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        count = await wipe_user_memories(user_id=user_id, bot_id=bot_instance.bot_id)
        await interaction.response.send_message(
            f"Privacy confirmed: {count} memories associated with your account have been deleted from this bot.",
            ephemeral=True
        )

    @bot.tree.command(name="ping", description="Checks the bot responsiveness and gateway latency.")
    async def cmd_ping(interaction: discord.Interaction):
        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(
            f"🏓 Pong! Discord Gateway Latency: `{latency_ms}ms`",
            ephemeral=True
        )

    @bot.tree.command(name="status", description="Displays current bot status and configuration.")
    async def cmd_status(interaction: discord.Interaction):
        config = bot_instance.config
        chain_len = len(config.get("endpoint_chain", []))
        trigger_mode = config.get("trigger_mode", "keywords")
        reply_policy = config.get("reply_policy", "ai_choice")
        
        embed = discord.Embed(
            title=f"🤖 Bot Status: {config.get('name')}",
            color=0x3b82f6
        )
        embed.add_field(name="Trigger Mode", value=f"`{trigger_mode}`", inline=True)
        embed.add_field(name="Reply Policy", value=f"`{reply_policy}`", inline=True)
        embed.add_field(name="Endpoints in Chain", value=f"`{chain_len}`", inline=True)
        embed.add_field(name="Cooldown", value=f"`{config.get('cooldown_seconds', 3)}s`", inline=True)
        embed.add_field(name="Anti-Loop Ignore Bots", value=f"`{config.get('ignore_bots', True)}`", inline=True)
        embed.set_footer(text="Mimic")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
