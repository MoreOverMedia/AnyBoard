import discord
from discord.ext import commands
from discord import app_commands
import os
import sqlite3
from dotenv import load_dotenv
import emoji as emoji_lib

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
STARBOARD_CHANNEL_NAME = os.getenv("STARBOARD_CHANNEL_NAME", "starboard")
DEFAULT_THRESHOLD = int(os.getenv("STAR_THRESHOLD", 5))

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------
# DATABASE
# --------------------
DB_PATH = "/app/data/starboard.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL;")

db.execute("""
CREATE TABLE IF NOT EXISTS starboard (
    message_id INTEGER,
    emoji TEXT,
    starboard_message_id INTEGER,
    webhook_url TEXT,
    PRIMARY KEY (message_id, emoji)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    star_threshold INTEGER
)
""")

db.commit()

# --------------------
# DB HELPERS
# --------------------
def db_get(message_id, emoji):
    row = db.execute(
        "SELECT starboard_message_id FROM starboard WHERE message_id=? AND emoji=?",
        (message_id, emoji)
    ).fetchone()
    return row[0] if row else None

def db_set(message_id, emoji, sb_id, webhook_url):
    db.execute(
        "INSERT OR REPLACE INTO starboard VALUES (?, ?, ?, ?)",
        (message_id, emoji, sb_id, webhook_url)
    )
    db.commit()

def db_get_webhook(message_id, emoji):
    row = db.execute(
        "SELECT webhook_url FROM starboard WHERE message_id=? AND emoji=?",
        (message_id, emoji)
    ).fetchone()
    return row[0] if row else None

def db_get_original_entries_by_starboard(sb_id):
    return db.execute(
        "SELECT message_id, emoji FROM starboard WHERE starboard_message_id=?",
        (sb_id,)
    ).fetchall()

def get_guild_threshold(guild_id):
    row = db.execute(
        "SELECT star_threshold FROM guild_settings WHERE guild_id=?",
        (guild_id,)
    ).fetchone()
    return row[0] if row else DEFAULT_THRESHOLD

def set_guild_threshold(guild_id, count):
    db.execute(
        "INSERT OR REPLACE INTO guild_settings VALUES (?, ?)",
        (guild_id, count)
    )
    db.commit()

# --------------------
# EMOJI HELPERS
# --------------------
def is_custom_emoji(e): return e.startswith("<:")

def unicode_emoji_url(e):
    code = "-".join(f"{ord(c):x}" for c in e)
    return f"https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{code}.png"

def emoji_image_url(e):
    if is_custom_emoji(e):
        eid = e.split(":")[-1][:-1]
        return f"https://cdn.discordapp.com/emojis/{eid}.png"
    return unicode_emoji_url(e)

def emoji_name(e):
    if is_custom_emoji(e):
        return e.split(":")[1].capitalize()
    return emoji_lib.demojize(e).strip(":").replace("_", "").title()

# --------------------
# THREAD SAFE HELPERS
# --------------------
async def get_channel_or_thread(guild, cid):
    ch = guild.get_channel(cid)
    if ch:
        return ch
    thread = guild.get_thread(cid)
    if thread:
        return thread
    try:
        return await guild.fetch_channel(cid)
    except discord.NotFound:
        return None

async def find_message(guild, mid):
    for ch in guild.text_channels:
        try:
            return await ch.fetch_message(mid)
        except (discord.NotFound, discord.Forbidden):
            pass
        for t in ch.threads:
            try:
                return await t.fetch_message(mid)
            except (discord.NotFound, discord.Forbidden):
                pass
    return None

# --------------------
# REACTION COUNT
# --------------------
async def reaction_count(original, sb_msg, emoji):
    users = set()

    async def collect(msg, allowed):
        for r in msg.reactions:
            if str(r.emoji) in allowed:
                async for u in r.users():
                    if not u.bot and u.id != original.author.id:
                        users.add(u.id)

    await collect(original, {emoji})
    if sb_msg:
        await collect(sb_msg, {"⭐" if is_custom_emoji(emoji) else emoji})

    return len(users)

# --------------------
# STARBOARD
# --------------------
async def update_starboard(msg, guild, emoji):
    starboard = discord.utils.get(
        guild.channels,
        name=STARBOARD_CHANNEL_NAME,
        type=discord.ChannelType.text
    )
    if not starboard or msg.channel.id == starboard.id:
        return

    threshold = get_guild_threshold(guild.id)
    existing = db_get(msg.id, emoji)

    sb_msg = None
    if existing:
        try:
            sb_msg = await starboard.fetch_message(existing)
        except (discord.NotFound, discord.Forbidden):
            pass

    count = await reaction_count(msg, sb_msg, emoji)
    if not existing and count < threshold:
        return

    embed = discord.Embed(
        description=f"{emoji} **{count}**\n{msg.content or '*[No text]*'}\n\n[Jump]({msg.jump_url})",
        timestamp=msg.created_at,
        color=discord.Color.dark_gray()
    )
    embed.set_author(
        name=f"{msg.author.display_name} in #{msg.channel.name}",
        icon_url=msg.author.display_avatar.url
    )

    if msg.attachments:
        embed.set_image(url=msg.attachments[0].url)

    webhook_url = db_get_webhook(msg.id, emoji)
    if not webhook_url:
        hooks = await starboard.webhooks()
        webhook = hooks[0] if hooks else await starboard.create_webhook(name="starboard")
        webhook_url = webhook.url

    webhook = discord.Webhook.from_url(webhook_url, client=bot)

    if sb_msg:
        await webhook.edit_message(sb_msg.id, embed=embed)
        return

    sent = await webhook.send(
        embed=embed,
        username=f"{emoji_name(emoji)}Board",
        avatar_url=emoji_image_url(emoji),
        wait=True
    )

    db_set(msg.id, emoji, sent.id, webhook_url)
    await sent.add_reaction(emoji if not is_custom_emoji(emoji) else "⭐")

# --------------------
# EVENTS
# --------------------
@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is None or payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    channel = await get_channel_or_thread(guild, payload.channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden):
        return

    starboard = discord.utils.get(guild.channels, name=STARBOARD_CHANNEL_NAME)

    if starboard and channel.id == starboard.id:
        for mid, emo in db_get_original_entries_by_starboard(msg.id):
            original = await find_message(guild, mid)
            if original:
                await update_starboard(original, guild, emo)
    else:
        await update_starboard(msg, guild, str(payload.emoji))

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    channel = await get_channel_or_thread(guild, payload.channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden):
        return

    starboard = discord.utils.get(guild.channels, name=STARBOARD_CHANNEL_NAME)

    if starboard and channel.id == starboard.id:
        for mid, emo in db_get_original_entries_by_starboard(msg.id):
            original = await find_message(guild, mid)
            if original:
                await update_starboard(original, guild, emo)
    else:
        await update_starboard(msg, guild, str(payload.emoji))

# --------------------
# SLASH COMMAND
# --------------------
@bot.tree.command(name="starthreshold")
async def starthreshold(interaction: discord.Interaction, count: int | None = None):
    if count is None:
        await interaction.response.send_message(
            f"⭐ Threshold: **{get_guild_threshold(interaction.guild_id)}**",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "Missing permission: Manage Server",
            ephemeral=True
        )
        return

    if count < 1:
        await interaction.response.send_message(
            "Threshold must be at least 1.",
            ephemeral=True
        )
        return

    set_guild_threshold(interaction.guild_id, count)
    await interaction.response.send_message(
        f"⭐ Threshold set to **{count}**",
        ephemeral=True
    )

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
