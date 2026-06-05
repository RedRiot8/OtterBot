import os
import asyncio
import random
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database as db

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Bomb mini-game state
bomb_users: set[int] = set()
bomb_task: asyncio.Task | None = None

CLASSES = {
    "Hero": {
        "str": 2,
        "int": 2,
        "arc": 1,
        "description": (
            "The balanced warrior. Equally capable in any situation — "
            "not the best at anything, never the worst."
        ),
    },
    "Mage": {
        "str": 1,
        "int": 3,
        "arc": 1,
        "description": (
            "The scholar. Outthinks every obstacle but struggles when "
            "brute force is the only answer."
        ),
    },
    "Bard": {
        "str": 1,
        "int": 1,
        "arc": 3,
        "description": (
            "The wildcard. Thrives on chaos and luck — incredible highs, "
            "catastrophic lows. Not for the faint hearted."
        ),
    },
    "Rogue": {
        "str": 2,
        "int": 1,
        "arc": 2,
        "description": (
            "The phantom. Blends strength and cunning — unpredictable, "
            "adaptable, dangerous in the right hands."
        ),
    },
    "Drakon": {
        "str": 3,
        "int": 1,
        "arc": 1,
        "description": (
            "The destroyer. Unmatched in raw power but limited elsewhere. "
            "High risk, high reward."
        ),
    },
}

STAT_FIELD_MAP = {
    "STR": "str_stat",
    "INT": "int_stat",
    "ARC": "arc_stat",
    "COINS": "coins",
    "MAP_POSITION": "map_position",
}

HELP_TEXT = """📖 SERVER GAMES — COMMAND LIST

━━━━━━━━━━━━━━━━━━━━━━
🔐 GAMEMASTER COMMANDS
━━━━━━━━━━━━━━━━━━━━━━

/servergames start
Start a new season. Bot will ask for the season
number before launching. Errors if season already active.

/servergames end
End the current season. Posts farewell message
in announcement channel.

/servergames configure
Set announcement and log channels. Optionally set the
Gamemaster role and Server Games player role.

/servergames status
View the current season number, start date, and signup count.
Open to all server members.

/setevent
Create a new event. Fields: Name, Description, Stat,
Threshold, Reward Type, Reward Amount.
Event ID is auto generated. Posts to announcement channel.

/announce [message]
Post a message through the bot to the announcement channel.

/stat add @user
Adjust a player's stats.
Fields: Stat (STR/INT/ARC/Coins/Map Position),
Amount (positive to add, negative to reduce).

/endevent [eventID]
End a specific active event by its ID.

━━━━━━━━━━━━━━━━━━━━━━
🎮 PLAYER COMMANDS
━━━━━━━━━━━━━━━━━━━━━━

/servergames signup
Sign up for the current season. Bot shows class
descriptions and a dropdown to choose yours.
Grants the Server Games role when configured.

/roll [eventID]
Requires signup.
Roll for an active event. 2 dice + your relevant
stat vs the event threshold. One roll per event.
Result posted publicly.

/showevents
View all currently active events. Requires signup.

/stats
View your own stats — Class, STR, INT, ARC,
Map Position, Coins. Requires signup.

/stats @user
View another player's stats. Coins are hidden. Requires signup.

/servergames help
Show this command list.

━━━━━━━━━━━━━━━━━━━━━━
💡 Need help? Contact a Gamemaster.
━━━━━━━━━━━━━━━━━━━━━━"""


def player_label(player) -> str:
    return f"{player['class_name']} {player['discord_username']}"


def reward_announcement_text(reward_type: str, reward_amount: int) -> str:
    if reward_type == "COINS":
        return "Coins"
    if reward_type == "MAP_POSITION":
        return f"Map Position +{reward_amount}"
    return f"{reward_type} +{reward_amount}"


def timestamp_label() -> str:
    return datetime.now().strftime("%I:%M %p").lstrip("0")


async def send_log(season, log_type: str, actor: str, command: str, details: str):
    if not season or not season["log_channel_id"]:
        return
    channel = bot.get_channel(int(season["log_channel_id"]))
    if channel is None:
        return
    await channel.send(
        f"[{timestamp_label()}] {log_type} — {actor} used {command}\n{details}"
    )


async def send_announcement(season, message: str):
    if not season or not season["announcement_channel_id"]:
        return
    channel = bot.get_channel(int(season["announcement_channel_id"]))
    if channel is None:
        return
    await channel.send(message)


PERMISSION_DENIED_MSG = (
    "❌ You don't have the necessary permissions to execute this command."
)

NOT_SIGNED_UP_MSG = (
    "❌ You must sign up for the current season using /servergames signup first."
)


def is_server_admin(interaction: discord.Interaction) -> bool:
    perms = interaction.user.guild_permissions
    return perms.administrator or perms.manage_guild


def has_gamemaster_role(interaction: discord.Interaction) -> bool:
    season = db.get_active_season()
    if not season or not season["gamemaster_role_id"]:
        return False
    if not isinstance(interaction.user, discord.Member):
        return False
    role_id = int(season["gamemaster_role_id"])
    return any(role.id == role_id for role in interaction.user.roles)


def is_gamemaster(interaction: discord.Interaction) -> bool:
    return is_server_admin(interaction) or has_gamemaster_role(interaction)


def format_season_start(created_at: str) -> str:
    try:
        dt = datetime.fromisoformat(created_at)
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except ValueError:
        return created_at


async def assign_player_role(member: discord.Member, season) -> None:
    if not season["player_role_id"]:
        return
    role = member.guild.get_role(int(season["player_role_id"]))
    if role is None:
        return
    try:
        await member.add_roles(role, reason="Server Games signup")
    except discord.HTTPException:
        pass


def get_player_stat_value(player, stat: str) -> int:
    return player[STAT_FIELD_MAP[stat]]


def apply_reward(player, reward_type: str, reward_amount: int) -> tuple[str, int, int]:
    field = STAT_FIELD_MAP[reward_type]
    old_value = player[field]
    new_value = old_value + reward_amount
    db.update_player_field(player["id"], field, new_value)
    return field, old_value, new_value


servergames = app_commands.Group(
    name="servergames", description="Server Games seasonal RPG"
)


@servergames.command(name="start", description="Start a new Server Games season")
@app_commands.describe(season_number="The season number to start")
async def servergames_start(
    interaction: discord.Interaction, season_number: app_commands.Range[int, 1, 9999]
):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    active = db.get_active_season()
    if active:
        await interaction.response.send_message(
            f"❌ Server Games Season {active['season_number']} is already active.",
            ephemeral=True,
        )
        return

    existing = db.get_season_by_number(season_number)
    if existing:
        next_number = db.get_next_season_number()
        await interaction.response.send_message(
            (
                f"❌ Season {season_number} already exists. "
                f"Use a new season number (e.g. {next_number})."
            ),
            ephemeral=True,
        )
        return

    season = db.create_season(season_number)
    await interaction.response.send_message(
        f"✅ Server Games Season {season_number} has been started.", ephemeral=True
    )

    await send_announcement(
        season,
        (
            f"📣 SERVER GAMES — SEASON {season_number} HAS BEGUN!\n\n"
            "The new season is now live. Sign up using /servergames signup "
            "and choose your class.\nGood luck to all players."
        ),
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/servergames start",
        f"Season {season_number} is now active.",
    )


@servergames.command(name="end", description="End the current Server Games season")
async def servergames_end(interaction: discord.Interaction):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    db.end_season(season["id"])
    await interaction.response.send_message(
        f"✅ Server Games Season {season['season_number']} has ended.",
        ephemeral=True,
    )

    await send_announcement(
        season,
        (
            f"📣 SERVER GAMES — SEASON {season['season_number']} HAS ENDED.\n\n"
            "Thank you for participating!"
        ),
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/servergames end",
        f"Season {season['season_number']} has ended.",
    )


@servergames.command(name="status", description="View current season info")
async def servergames_status(interaction: discord.Interaction):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    signup_count = db.count_players(season["id"])
    started = format_season_start(season["created_at"])
    await interaction.response.send_message(
        (
            f"📣 SERVER GAMES — SEASON {season['season_number']}\n\n"
            f"Started: {started}\n"
            f"Players signed up: {signup_count}"
        ),
        ephemeral=True,
    )


@servergames.command(
    name="configure", description="Set announcement and log channels"
)
@app_commands.describe(
    announcement_channel="Channel for public announcements",
    log_channel="Channel for Gamemaster logs",
    gamemaster_role="Gamemaster role (optional)",
    player_role="Server Games player role assigned on signup (optional)",
)
async def servergames_configure(
    interaction: discord.Interaction,
    announcement_channel: discord.TextChannel,
    log_channel: discord.TextChannel,
    gamemaster_role: discord.Role = None,
    player_role: discord.Role = None,
):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    gamemaster_role_id = str(gamemaster_role.id) if gamemaster_role else None
    player_role_id = str(player_role.id) if player_role else None
    db.configure_season(
        season["id"],
        str(announcement_channel.id),
        str(log_channel.id),
        gamemaster_role_id,
        player_role_id,
    )
    season = db.get_active_season()

    lines = [
        "✅ Configuration saved.",
        f"Announcement Channel: {announcement_channel.mention}",
        f"Log Channel: {log_channel.mention}",
    ]
    if gamemaster_role:
        lines.append(f"Gamemaster Role: {gamemaster_role.mention}")
    elif season["gamemaster_role_id"]:
        role = interaction.guild.get_role(int(season["gamemaster_role_id"]))
        role_label = role.mention if role else season["gamemaster_role_id"]
        lines.append(f"Gamemaster Role: {role_label}")
    if player_role:
        lines.append(f"Server Games Role: {player_role.mention}")
    elif season["player_role_id"]:
        role = interaction.guild.get_role(int(season["player_role_id"]))
        role_label = role.mention if role else season["player_role_id"]
        lines.append(f"Server Games Role: {role_label}")

    log_details = (
        f"Announcement channel set: {announcement_channel.mention}\n"
        f"Log channel set: {log_channel.mention}"
    )
    if gamemaster_role:
        log_details += f"\nGamemaster role set: {gamemaster_role.mention}"
    if player_role:
        log_details += f"\nServer Games role set: {player_role.mention}"

    await interaction.response.send_message("\n".join(lines), ephemeral=True)
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/servergames configure",
        log_details,
    )


class ClassSelect(discord.ui.Select):
    def __init__(self, season_number: int):
        options = [
            discord.SelectOption(
                label=class_name,
                description=(
                    f"STR {stats['str']} | INT {stats['int']} | ARC {stats['arc']}"
                )[:100],
            )
            for class_name, stats in CLASSES.items()
        ]
        super().__init__(
            placeholder="Choose your class",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.season_number = season_number

    async def callback(self, interaction: discord.Interaction):
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "❌ There is no active Server Games season. Stay tuned!",
                ephemeral=True,
            )
            return

        existing = db.get_player(str(interaction.user.id), season["id"])
        if existing:
            await interaction.response.send_message(
                (
                    f"❌ You are already signed up for Season {season['season_number']} "
                    f"as {player_label(existing)}."
                ),
                ephemeral=True,
            )
            return

        class_name = self.values[0]
        stats = CLASSES[class_name]
        player = db.create_player(
            str(interaction.user.id),
            interaction.user.display_name,
            season["id"],
            class_name,
            stats["str"],
            stats["int"],
            stats["arc"],
        )

        if isinstance(interaction.user, discord.Member):
            await assign_player_role(interaction.user, season)

        await interaction.response.send_message(
            f"✅ You joined Season {season['season_number']} as {player_label(player)}!",
            ephemeral=True,
        )

        await send_announcement(
            season,
            (
                f"⚔️ Welcome to Server Games Season {season['season_number']}\n"
                f"{player_label(player)} has joined the battle!"
            ),
        )
        await send_log(
            season,
            "PLAYER",
            player_label(player),
            "/servergames signup",
            (
                f"Class selected: {class_name} | STR: {stats['str']} | "
                f"INT: {stats['int']} | ARC: {stats['arc']}"
            ),
        )


class ClassSelectView(discord.ui.View):
    def __init__(self, season_number: int):
        super().__init__(timeout=120)
        self.add_item(ClassSelect(season_number))


@servergames.command(name="signup", description="Sign up for the current season")
async def servergames_signup(interaction: discord.Interaction):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season. Stay tuned!", ephemeral=True
        )
        return

    existing = db.get_player(str(interaction.user.id), season["id"])
    if existing:
        await interaction.response.send_message(
            (
                f"❌ You are already signed up for Season {season['season_number']} "
                f"as {player_label(existing)}."
            ),
            ephemeral=True,
        )
        return

    class_lines = []
    for class_name, stats in CLASSES.items():
        class_lines.append(
            f"**{class_name}** — STR {stats['str']} | INT {stats['int']} | "
            f"ARC {stats['arc']}\n{stats['description']}"
        )

    embed = discord.Embed(
        title=f"⚔️ CHOOSE YOUR CLASS — SERVER GAMES SEASON {season['season_number']}",
        description="\n\n".join(class_lines),
        color=discord.Color.blue(),
    )
    await interaction.response.send_message(
        embed=embed, view=ClassSelectView(season["season_number"]), ephemeral=True
    )


@servergames.command(name="help", description="Show the Server Games command list")
async def servergames_help(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(HELP_TEXT, ephemeral=True)
    if interaction.message:
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass


stat_group = app_commands.Group(name="stat", description="Gamemaster stat management")


@stat_group.command(name="add", description="Adjust a player's stats")
@app_commands.describe(
    user="Target player",
    stat="Stat to adjust",
    amount="Positive to add, negative to reduce",
)
@app_commands.choices(
    stat=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
        app_commands.Choice(name="COINS", value="COINS"),
        app_commands.Choice(name="MAP_POSITION", value="MAP_POSITION"),
    ]
)
async def stat_add(
    interaction: discord.Interaction,
    user: discord.Member,
    stat: app_commands.Choice[str],
    amount: int,
):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    player = db.get_player(str(user.id), season["id"])
    if not player:
        await interaction.response.send_message(
            "❌ That player is not signed up for the current season.", ephemeral=True
        )
        return

    field = STAT_FIELD_MAP[stat.value]
    old_value = player[field]
    new_value = old_value + amount
    if new_value < 0:
        await interaction.response.send_message(
            "❌ Stat cannot go below 0.", ephemeral=True
        )
        return

    db.update_player_field(player["id"], field, new_value)
    label = player_label(player)
    stat_name = stat.value if stat.value != "MAP_POSITION" else "Map Position"

    await interaction.response.send_message(
        f"✅ Stats updated.\n{label} — {stat_name}: {old_value} → {new_value}",
        ephemeral=True,
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/stat add",
        (
            f"Target: {label} | Stat: {stat_name} | Amount: {amount:+d} | "
            f"New value: {new_value}"
        ),
    )


@bot.tree.command(name="setevent", description="Create a new Server Games event")
@app_commands.describe(
    name="Event name",
    description="Event flavour text",
    stat="Stat checked during rolls",
    threshold="Number players must beat",
    reward_type="Reward granted on pass",
    reward_amount="How much is awarded on pass",
)
@app_commands.choices(
    stat=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
    ],
    reward_type=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
        app_commands.Choice(name="COINS", value="COINS"),
        app_commands.Choice(name="MAP_POSITION", value="MAP_POSITION"),
    ],
)
async def setevent(
    interaction: discord.Interaction,
    name: str,
    description: str,
    stat: app_commands.Choice[str],
    threshold: app_commands.Range[int, 1, 100],
    reward_type: app_commands.Choice[str],
    reward_amount: app_commands.Range[int, 1, 9999],
):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    event_code = db.next_event_code(season["id"])
    event = db.create_event(
        season["id"],
        event_code,
        name,
        description,
        stat.value,
        threshold,
        reward_type.value,
        reward_amount,
    )

    await interaction.response.send_message(
        f"✅ Event created: {name} ({event_code})", ephemeral=True
    )

    reward_text = reward_announcement_text(reward_type.value, reward_amount)
    await send_announcement(
        season,
        (
            f"📣 NEW EVENT — {name}\n\n"
            f"{description}\n\n"
            f"Stat Check: {stat.value} | Threshold: {threshold}\n"
            f"Reward: {reward_text}\n"
            f"Event ID: {event_code}\n\n"
            f"Use /roll {event_code} to participate!"
        ),
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/setevent",
        (
            f"Event created: {name} | ID: {event_code}\n"
            f"Stat: {stat.value} | Threshold: {threshold} | "
            f"Reward: {reward_type.value} +{reward_amount}"
        ),
    )


@bot.tree.command(name="announce", description="Post a message to the announcement channel")
@app_commands.describe(message="Message to announce")
async def announce_cmd(interaction: discord.Interaction, message: str):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    await interaction.response.send_message("✅ Announcement posted.", ephemeral=True)
    await send_announcement(season, f"📣 {message}")
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/announce",
        f'Message: "{message}"',
    )


@bot.tree.command(name="showevents", description="Show all active Server Games events")
async def showevents_cmd(interaction: discord.Interaction):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    if not db.get_player(str(interaction.user.id), season["id"]):
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return

    events = db.get_active_events(season["id"])
    if not events:
        await interaction.response.send_message(
            "No active events right now.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"Active Events — Season {season['season_number']}",
        color=discord.Color.green(),
    )
    for event in events:
        reward_text = reward_announcement_text(
            event["reward_type"], event["reward_amount"]
        )
        embed.add_field(
            name=f"[{event['event_id']}] {event['name']}",
            value=(
                f"{event['description']}\n"
                f"Stat: {event['stat']} | Threshold: {event['threshold']}\n"
                f"Reward: {reward_text}"
            ),
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="endevent", description="End a specific active event")
@app_commands.describe(event_id="Event ID, e.g. 001")
async def endevent_cmd(interaction: discord.Interaction, event_id: str):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    normalized_id = db.normalize_event_code(event_id)
    event = db.deactivate_event(event_id, season["id"])
    if not event:
        await interaction.response.send_message(
            f"❌ Event {normalized_id} not found.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ Event {normalized_id} — {event['name']} has been ended.",
        ephemeral=True,
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/endevent",
        f"Event ended: {event['name']} | ID: {normalized_id}",
    )


@bot.tree.command(name="roll", description="Roll for a Server Games event")
@app_commands.describe(event_id="Event ID, e.g. 001")
async def roll_cmd(interaction: discord.Interaction, event_id: str):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    normalized_id = db.normalize_event_code(event_id)
    event = db.get_event_by_code(event_id, season["id"])
    if not event:
        await interaction.response.send_message(
            f"❌ Event {normalized_id} not found.", ephemeral=True
        )
        return

    player = db.get_player(str(interaction.user.id), season["id"])
    if not player:
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return

    if db.has_player_rolled(player["id"], event["id"]):
        await interaction.response.send_message(
            "❌ You have already participated in this event.", ephemeral=True
        )
        return

    dice1 = random.randint(1, 6)
    dice2 = random.randint(1, 6)
    stat_value = get_player_stat_value(player, event["stat"])
    dice_sum = dice1 + dice2
    total = dice_sum + stat_value
    passed = total > event["threshold"]
    result = "PASS" if passed else "FAIL"

    db.create_roll(
        player["id"],
        event["id"],
        dice1,
        dice2,
        stat_value,
        total,
        event["threshold"],
        result,
    )

    label = player_label(player)
    lines = [
        f"🎲 SERVER GAMES — {event['name']}",
        "",
        label,
        f"Dice Roll: {dice1} + {dice2} = {dice_sum}",
        f"{event['stat']} Stat: +{stat_value}",
        f"Result Check: {dice_sum} + {stat_value} = {total} > {event['threshold']}",
        "",
        "✅ PASS" if passed else "❌ FAIL",
    ]

    log_details = (
        f"Dice: {dice1}+{dice2}={dice_sum} | {event['stat']} Stat: +{stat_value} | "
        f"Total: {total}\nThreshold: {event['threshold']} | Result: {result}"
    )

    if passed:
        field, old_value, new_value = apply_reward(
            player, event["reward_type"], event["reward_amount"]
        )
        reward_type = event["reward_type"]

        if reward_type == "COINS":
            lines.append("🏆 Reward: Coins awarded")
            log_details += (
                f"\nReward: COINS +{event['reward_amount']} | "
                f"New balance: {new_value}"
            )
        elif reward_type == "MAP_POSITION":
            lines.append("🏆 Reward: Map Position updated")
            lines.append(
                f"{label}'s Map Position has been updated: {old_value} → {new_value}"
            )
            log_details += (
                f"\nReward: MAP_POSITION +{event['reward_amount']} | "
                f"New value: {new_value}"
            )
        else:
            lines.append(f"🏆 Reward: {reward_type} +{event['reward_amount']}")
            lines.append(
                f"{label}'s {reward_type} has been updated: {old_value} → {new_value}"
            )
            log_details += (
                f"\nReward: {reward_type} +{event['reward_amount']} | "
                f"New value: {new_value}"
            )

    await interaction.response.send_message("\n".join(lines))
    await send_log(
        season,
        "PLAYER",
        label,
        f"/roll {event['event_id']}",
        log_details,
    )


@bot.tree.command(name="stats", description="View Server Games player stats")
@app_commands.describe(user="Player to view (optional)")
async def stats_cmd(interaction: discord.Interaction, user: discord.Member = None):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    if not db.get_player(str(interaction.user.id), season["id"]):
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return

    target = user or interaction.user
    player = db.get_player(str(target.id), season["id"])
    if not player:
        await interaction.response.send_message(
            "❌ That player is not signed up for the current season.", ephemeral=True
        )
        return

    label = player_label(player)
    viewing_self = target.id == interaction.user.id
    gamemaster_view = is_gamemaster(interaction)

    if viewing_self:
        text = (
            f"📊 YOUR STATS — Season {season['season_number']}\n\n"
            f"Class: {player['class_name']}\n"
            f"Username: {player['discord_username']}\n\n"
            f"STR: {player['str_stat']}\n"
            f"INT: {player['int_stat']}\n"
            f"ARC: {player['arc_stat']}\n\n"
            f"Map Position: {player['map_position']}\n"
            f"Coins: {player['coins']}"
        )
    elif gamemaster_view:
        text = (
            f"📊 STATS — {label} [GAMEMASTER VIEW]\n\n"
            f"Class: {player['class_name']}\n\n"
            f"STR: {player['str_stat']}\n"
            f"INT: {player['int_stat']}\n"
            f"ARC: {player['arc_stat']}\n\n"
            f"Map Position: {player['map_position']}\n"
            f"Coins: {player['coins']}"
        )
    else:
        text = (
            f"📊 STATS — {label}\n\n"
            f"Class: {player['class_name']}\n\n"
            f"STR: {player['str_stat']}\n"
            f"INT: {player['int_stat']}\n"
            f"ARC: {player['arc_stat']}\n\n"
            f"Map Position: {player['map_position']}"
        )

    await interaction.response.send_message(text, ephemeral=True)

    if user and user.id != interaction.user.id:
        await send_log(
            season,
            "PLAYER",
            interaction.user.display_name,
            f"/stats @{player['discord_username']}",
            "",
        )


async def bomb_countdown(channel: discord.TextChannel):
    global bomb_users, bomb_task
    try:
        await asyncio.sleep(20)
    except asyncio.CancelledError:
        return

    if bomb_users:
        await channel.send("Bomb has been diffused.")
    bomb_users = set()
    bomb_task = None


@bot.command(name="bomb")
async def bomb(ctx: commands.Context):
    """Bomb mini-game: 20 unique users to explode, 20s inactivity to diffuse."""
    global bomb_users, bomb_task

    user_id = ctx.author.id
    first_activation = len(bomb_users) == 0

    bomb_users.add(user_id)

    if bomb_task is not None and not bomb_task.done():
        bomb_task.cancel()
    bomb_task = asyncio.create_task(bomb_countdown(ctx.channel))

    if first_activation:
        await ctx.send("Bomb has been activated!")

    remaining = 20 - len(bomb_users)

    if remaining <= 0:
        await ctx.send("💥 The server has exploded! 💥")
        bomb_users = set()
        if bomb_task is not None and not bomb_task.done():
            bomb_task.cancel()
        bomb_task = None
    else:
        await ctx.send(f"[{remaining}] more users need to arm the bomb.")


@bot.event
async def on_ready():
    db.init_db()
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


bot.tree.add_command(servergames)
bot.tree.add_command(stat_group)


if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
