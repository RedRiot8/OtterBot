import json
import os
import re
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


def stat_modifier(stat_value: int) -> int:
    return max(0, min(stat_value - 1, 5))

HELP_TEXT_GM = """📖 SERVER GAMES — GAMEMASTER COMMANDS

━━━━━━━━━━━━━━━━━━━━━━
🔐 GAMEMASTER COMMANDS
━━━━━━━━━━━━━━━━━━━━━━

/servergames start [season] [#announce] [#logs] (@gm-role) (@player-role)
Start a new season and configure channels in one step.
Posts the season announcement immediately.

/servergames end
End the current season. Posts farewell message to the announcement channel.

/servergames configure [#announce] [#logs] (@gm-role) (@player-role)
Update channels or roles for the active season.

/setevent — Create a new event.
Required: Name, Description, Stat, Threshold, Reward Type, Reward Amount.
Optional: Reward Item, Secret type, Secret Targets, Reward 2, Fail/Success Message, Map Position.

/editevent [eventID] — Edit an existing event. Only provided fields are updated.

/additem [name] [description] — Add an item. Optional: scavengable, rarity (COMMON/RARE/EPIC).

/edititem [item] — Edit an item's name, description, scavengable flag, or rarity.

/removeitem — Remove an item from the pool (with confirmation).

/stat add @user [stat] [amount]
Adjust a player's stats: STR / INT / ARC / COINS / MAP_POSITION / INVENTORY

/announce [message] — Post a custom message to the announcement channel.

/endevent [eventID] — Deactivate a specific event by its ID.

/servergames status — View season start date and signup count."""

HELP_TEXT_PLAYER = """📖 SERVER GAMES — PLAYER COMMANDS

━━━━━━━━━━━━━━━━━━━━━━
🎮 PLAYER COMMANDS
━━━━━━━━━━━━━━━━━━━━━━

/servergames signup
Sign up for the current season. Choose your class from a dropdown.
Assigns the Server Games role if configured.

/roll [eventID]
Roll for an event. 2 dice + your stat vs the threshold.
One roll per event per player.
Public events: result posted in channel.
Secret events: result sent to your DMs.

/trade [your item] [coins]
List one of your own items for sale at 1–50 coins.
Posted publicly — any signed-up player can buy it.
Listing expires after 5 minutes; you can cancel early.

/challenge @player [stat] [wager]
Challenge another player to a stat duel. Both roll 2d6 + chosen stat.
Higher total wins the wagered coins. Tie = draw, no coins transfer.
Both players must have enough coins. Result is announced.

/scavenge
Search for loot — bot picks a random stat and threshold (1–20).
Roll 2d6 + your stat to claim a weighted-random item from the season pool.
Rarity: Common 70% / Rare 25% / Epic 5%. 1-hour cooldown.

/leaderboard — Post top 5 map positions to the announcement channel.

/showevents — View all active public events. Requires signup.

/stats — View your own stats: Class, STR, INT, ARC, Map Position, Coins, Inventory.

/stats @user — View another player's stats.
Coins and Inventory are hidden from other players.

/servergames help — Show this command list.

━━━━━━━━━━━━━━━━━━━━━━
💡 Need help? Contact an admin or GM.
━━━━━━━━━━━━━━━━━━━━━━"""


def parse_targets_with_type(raw: str) -> list[dict]:
    """Parse comma-separated @mentions into [{"id": int, "type": "ROLE"|"PLAYER"}]."""
    results = []
    for part in raw.split(","):
        part = part.strip()
        role_match = re.match(r"<@&(\d+)>", part)
        if role_match:
            results.append({"id": int(role_match.group(1)), "type": "ROLE"})
            continue
        user_match = re.match(r"<@!?(\d+)>", part)
        if user_match:
            results.append({"id": int(user_match.group(1)), "type": "PLAYER"})
            continue
        if part.isdigit():
            results.append({"id": int(part), "type": "PLAYER"})
    return results


def resolve_target_members(event, guild: discord.Guild) -> list[discord.Member]:
    """Resolve secret_targets from an event row into a deduplicated list of Members.
    Handles both the new per-target typed format and the legacy flat-ID format."""
    raw_json = event["secret_targets"]
    if not raw_json:
        return []
    targets = json.loads(raw_json)
    members: list[discord.Member] = []

    if targets and isinstance(targets[0], dict):
        # New format: [{"id": "123", "type": "ROLE"}, ...]
        for t in targets:
            if t["type"] == "ROLE":
                role = guild.get_role(int(t["id"]))
                if role:
                    members.extend(role.members)
            else:
                member = guild.get_member(int(t["id"]))
                if member:
                    members.append(member)
    else:
        # Legacy format: ["123", "456"] with a separate secret_target_type column
        target_type = event["secret_target_type"] or "PLAYER"
        for id_str in targets:
            if target_type == "ROLE":
                role = guild.get_role(int(id_str))
                if role:
                    members.extend(role.members)
            else:
                member = guild.get_member(int(id_str))
                if member:
                    members.append(member)

    seen: set[int] = set()
    unique: list[discord.Member] = []
    for m in members:
        if m.id not in seen:
            seen.add(m.id)
            unique.append(m)
    return unique


def player_can_access_secret(interaction: discord.Interaction, event) -> bool:
    """Check whether the interacting user is a valid target for a secret event."""
    raw_json = event["secret_targets"]
    if not raw_json:
        return False
    targets = json.loads(raw_json)

    if targets and isinstance(targets[0], dict):
        # New format
        for t in targets:
            if t["type"] == "PLAYER" and interaction.user.id == int(t["id"]):
                return True
            if t["type"] == "ROLE" and isinstance(interaction.user, discord.Member):
                if any(r.id == int(t["id"]) for r in interaction.user.roles):
                    return True
    else:
        # Legacy format
        target_type = event["secret_target_type"] or "PLAYER"
        ids = [int(s) for s in targets if str(s).isdigit()]
        if target_type == "PLAYER":
            return interaction.user.id in ids
        if target_type == "ROLE" and isinstance(interaction.user, discord.Member):
            return any(r.id in ids for r in interaction.user.roles)
    return False


def player_label(player) -> str:
    return f"{player['class_name']} {player['discord_username']}"


def reward_announcement_text(reward_type: str, reward_amount: int | None) -> str:
    if reward_type == "COINS":
        return "Coins"
    if reward_type == "MAP_POSITION":
        return f"Map Position +{reward_amount}"
    if reward_type == "ITEM":
        return "Item awarded"
    return f"{reward_type} +{reward_amount}"


def timestamp_label() -> str:
    return datetime.now().strftime("%I:%M %p").lstrip("0")


async def send_log(season, log_type: str, actor: str, command: str, details: str):
    if not season or not season["log_channel_id"]:
        return
    channel = bot.get_channel(int(season["log_channel_id"]))
    if channel is None:
        return
    try:
        await channel.send(
            f"[{timestamp_label()}] {log_type} — {actor} used {command}\n{details}"
        )
    except discord.HTTPException:
        pass


async def send_announcement(season, message: str):
    if not season or not season["announcement_channel_id"]:
        return
    channel = bot.get_channel(int(season["announcement_channel_id"]))
    if channel is None:
        return
    try:
        await channel.send(message)
    except discord.HTTPException:
        pass


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
    except discord.HTTPException as e:
        print(f"[assign_player_role] Failed to assign player role to {member}: {e}")


def get_player_stat_value(player, stat: str) -> int:
    return player[STAT_FIELD_MAP[stat]]


def apply_reward(player, reward_type: str, reward_amount: int) -> tuple[str, int, int]:
    field = STAT_FIELD_MAP[reward_type]
    old_value = player[field]
    new_value = old_value + reward_amount
    db.update_player_field(player["id"], field, new_value)
    return field, old_value, new_value


def event_rewards(event) -> list[tuple[str, int]]:
    rewards = [(event["reward_type"], event["reward_amount"])]
    if event["reward_2_type"] and event["reward_2_amount"]:
        rewards.append((event["reward_2_type"], event["reward_2_amount"]))
    return rewards


def append_reward_result(
    lines: list[str],
    log_details: str,
    label: str,
    reward_type: str,
    reward_amount: int,
    old_value: int,
    new_value: int,
) -> str:
    if reward_type == "COINS":
        lines.append("🏆 Reward: Coins awarded")
        return (
            log_details
            + f"\nReward: COINS +{reward_amount} | New balance: {new_value}"
        )

    if reward_type == "MAP_POSITION":
        lines.append("🏆 Reward: Map Position updated")
        lines.append(
            f"{label}'s Map Position has been updated: {old_value} → {new_value}"
        )
        return (
            log_details
            + f"\nReward: MAP_POSITION +{reward_amount} | New value: {new_value}"
        )

    lines.append(f"🏆 Reward: {reward_type} +{reward_amount}")
    lines.append(f"{label}'s {reward_type} has been updated: {old_value} → {new_value}")
    return log_details + f"\nReward: {reward_type} +{reward_amount} | New value: {new_value}"


servergames = app_commands.Group(
    name="servergames", description="Server Games seasonal RPG"
)


# ── /servergames test — guided walkthrough infrastructure ────────────────────

active_test_session: "TestSession | None" = None


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


async def _ok(msg: str) -> tuple[bool, str]:
    return True, msg


async def poll_until(check, timeout: float = 20, interval: float = 1.5):
    """Poll `check()` until it returns a truthy value, or timeout. Returns that value or None."""
    elapsed = 0.0
    while elapsed < timeout:
        result = check()
        if result:
            return result
        await asyncio.sleep(interval)
        elapsed += interval
    return None


async def poll_until_change(getter, before, timeout: float = 20, interval: float = 1.5):
    """Poll `getter()` until its value differs from `before`, or timeout. Returns the new value or None."""
    elapsed = 0.0
    while elapsed < timeout:
        current = getter()
        if current != before:
            return current
        await asyncio.sleep(interval)
        elapsed += interval
    return None


def chunk_text(text: str, limit: int = 1900) -> list[str]:
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


class TestSession:
    def __init__(self, gm, log_channel, announcement_channel, gm_role, player_role, season_number):
        self.gm = gm
        self.log_channel = log_channel
        self.announcement_channel = announcement_channel
        self.gm_role = gm_role
        self.player_role = player_role
        self.season_number = season_number
        self.step_index = 0
        self.results: list[tuple[str, bool, str]] = []
        self.steps = build_test_steps(self)


def make_stat_change_step(title, command_name, instruction, stat_field, expected_delta, display_name, gm, season_fn, player_fn):
    snapshot: dict = {}

    def pre():
        p = player_fn()
        snapshot["before"] = _row_get(p, stat_field, 0)

    async def verify():
        s = season_fn()
        new_val = await poll_until_change(
            lambda: _row_get(db.get_player(str(gm.id), s["id"]), stat_field),
            snapshot.get("before"),
        )
        if new_val is None:
            return False, f"{display_name} didn't change from {snapshot.get('before')}."
        diff = new_val - snapshot["before"]
        if diff != expected_delta:
            return False, f"{display_name} changed by {diff:+d}, expected {expected_delta:+d} (now {new_val})."
        return True, f"{display_name} went from {snapshot['before']} to {new_val} as expected."

    return {
        "title": title,
        "command": command_name,
        "instruction": instruction,
        "pre": pre,
        "verify": verify,
    }


def build_test_steps(session: "TestSession") -> list[dict]:
    gm = session.gm
    ann = session.announcement_channel
    log = session.log_channel
    gm_role = session.gm_role
    player_role = session.player_role
    num = session.season_number

    def season():
        return db.get_active_season()

    def player():
        s = season()
        return db.get_player(str(gm.id), s["id"]) if s else None

    role_part = ""
    if gm_role:
        role_part += f" gamemaster_role:{gm_role.mention}"
    if player_role:
        role_part += f" player_role:{player_role.mention}"

    steps: list[dict] = []

    # 1. /servergames start ----------------------------------------------------
    async def verify_start():
        s = await poll_until(lambda: db.get_active_season())
        if not s:
            return False, "No active season found."
        problems = []
        if s["season_number"] != num:
            problems.append(f"season number is {s['season_number']}, expected {num}")
        if s["announcement_channel_id"] != str(ann.id):
            problems.append("announcement channel doesn't match")
        if s["log_channel_id"] != str(log.id):
            problems.append("log channel doesn't match")
        if gm_role and s["gamemaster_role_id"] != str(gm_role.id):
            problems.append("gamemaster role doesn't match")
        if player_role and s["player_role_id"] != str(player_role.id):
            problems.append("player role doesn't match")
        if problems:
            return False, "; ".join(problems)
        return True, f"Season {s['season_number']} is active with the expected channels and roles."

    steps.append({
        "title": "servergames start",
        "command": "servergames start",
        "instruction": (
            f"Run:\n`/servergames start season_number:{num} announcement_channel:{ann.mention} "
            f"log_channel:{log.mention}{role_part}`"
        ),
        "verify": verify_start,
    })

    # 2. /servergames configure -------------------------------------------------
    async def verify_configure():
        s = await poll_until(lambda: db.get_active_season())
        if not s:
            return False, "No active season found."
        if s["announcement_channel_id"] != str(ann.id) or s["log_channel_id"] != str(log.id):
            return False, "Configured channels don't match what was submitted."
        return True, "Season channels/roles re-confirmed via configure."

    steps.append({
        "title": "servergames configure",
        "command": "servergames configure",
        "instruction": (
            f"Run:\n`/servergames configure announcement_channel:{ann.mention} "
            f"log_channel:{log.mention}{role_part}`"
        ),
        "verify": verify_configure,
    })

    # 3-4. /additem -------------------------------------------------------------
    async def verify_additem_sword():
        s = season()
        items = await poll_until(lambda: [i for i in db.get_items(s["id"]) if i["name"] == "Test Sword"] or None)
        if not items:
            return False, "No item named 'Test Sword' found in the item pool."
        return True, "Item 'Test Sword' added to the pool."

    steps.append({
        "title": "additem (Test Sword)",
        "command": "additem",
        "instruction": "Run:\n`/additem name:Test Sword description:A blade used for testing event rewards`",
        "verify": verify_additem_sword,
    })

    async def verify_additem_coin():
        s = season()
        items = await poll_until(lambda: [i for i in db.get_items(s["id"]) if i["name"] == "Lucky Coin"] or None)
        if not items:
            return False, "No item named 'Lucky Coin' found in the item pool."
        return True, "Item 'Lucky Coin' added to the pool."

    steps.append({
        "title": "additem (Lucky Coin)",
        "command": "additem",
        "instruction": "Run:\n`/additem name:Lucky Coin description:A trinket used for testing inventory management`",
        "verify": verify_additem_coin,
    })

    # 5. /setevent — public, COINS reward (event 001) --------------------------
    async def verify_setevent_coins():
        s = season()
        ev = await poll_until(lambda: db.get_event_by_code("001", s["id"], active_only=False))
        if not ev:
            return False, "Event 001 not found."
        problems = []
        if ev["name"] != "Goblin Ambush":
            problems.append(f"name is '{ev['name']}', expected 'Goblin Ambush'")
        if ev["reward_type"] != "COINS" or ev["reward_amount"] != 50:
            problems.append("reward isn't COINS +50")
        if ev["is_secret"]:
            problems.append("event was created as secret, expected public")
        if not ev["success_message"] or not ev["fail_message"]:
            problems.append("success_message/fail_message weren't saved")
        if problems:
            return False, "; ".join(problems)
        return True, (
            "Event 001 'Goblin Ambush' created (STR check, COINS reward, with success/fail messages) "
            "and announced."
        )

    steps.append({
        "title": "setevent (public, COINS reward)",
        "command": "setevent",
        "instruction": (
            "Run:\n`/setevent name:Goblin Ambush description:A pack of goblins blocks the road. "
            "stat:STR threshold:3 reward_type:COINS reward_amount:50 "
            "success_message:You feel triumphant! fail_message:You limp away bruised.`\n"
            f"Check that the announcement appears in {ann.mention}."
        ),
        "verify": verify_setevent_coins,
    })

    # 6. /setevent — public, ITEM reward (event 002) ---------------------------
    async def verify_setevent_item():
        s = season()
        ev = await poll_until(lambda: db.get_event_by_code("002", s["id"], active_only=False))
        if not ev:
            return False, "Event 002 not found."
        problems = []
        if ev["name"] != "Dragon's Hoard":
            problems.append("name doesn't match \"Dragon's Hoard\"")
        if ev["reward_type"] != "ITEM" or not ev["reward_item_id"]:
            problems.append("reward isn't an ITEM reward")
        else:
            item = db.get_item_by_id(ev["reward_item_id"])
            if not item or item["name"] != "Test Sword":
                problems.append("reward item isn't 'Test Sword'")
        if problems:
            return False, "; ".join(problems)
        return True, "Event 002 \"Dragon's Hoard\" created with an ITEM reward (Test Sword) and announced."

    steps.append({
        "title": "setevent (public, ITEM reward)",
        "command": "setevent",
        "instruction": (
            "Run:\n`/setevent name:Dragon's Hoard description:A dragon guards a glittering hoard. "
            "stat:INT threshold:4 reward_type:ITEM reward_item:Test Sword`"
        ),
        "verify": verify_setevent_item,
    })

    # 7. /setevent — map-gated (event 003), should NOT announce ----------------
    async def verify_setevent_mapgate():
        s = season()
        ev = await poll_until(lambda: db.get_event_by_code("003", s["id"], active_only=False))
        if not ev:
            return False, "Event 003 not found."
        problems = []
        if ev["name"] != "Hidden Grove":
            problems.append("name doesn't match 'Hidden Grove'")
        if ev["map_position_required"] != 2:
            problems.append(f"map_position_required is {ev['map_position_required']}, expected 2")
        if ev["is_secret"]:
            problems.append("event was created as secret, expected public")
        if problems:
            return False, "; ".join(problems)
        return True, (
            "Event 003 'Hidden Grove' created with Map Position Required = 2. "
            f"Confirm NO announcement was posted to {ann.mention} for this one — "
            "map-gated events stay hidden until the player reaches that position."
        )

    steps.append({
        "title": "setevent (map-gated, no announcement)",
        "command": "setevent",
        "instruction": (
            "Run:\n`/setevent name:Hidden Grove description:A grove that only appears further down the road. "
            "stat:ARC threshold:4 reward_type:COINS reward_amount:75 map_position_required:2`\n"
            f"This should NOT post an announcement to {ann.mention}."
        ),
        "verify": verify_setevent_mapgate,
    })

    # 8. /setevent — secret targeting GM (event 004) ---------------------------
    async def verify_setevent_secret():
        s = season()
        ev = await poll_until(lambda: db.get_event_by_code("004", s["id"], active_only=False))
        if not ev:
            return False, "Event 004 not found."
        problems = []
        if ev["name"] != "Shadow Pact":
            problems.append("name doesn't match 'Shadow Pact'")
        if not ev["is_secret"]:
            problems.append("event isn't marked secret")
        targets = json.loads(ev["secret_targets"]) if ev["secret_targets"] else []
        ids = []
        for t in targets:
            if isinstance(t, dict):
                ids.append(int(t["id"]))
            else:
                ids.append(int(t))
        if gm.id not in ids:
            problems.append("you weren't included in the secret targets")
        if problems:
            return False, "; ".join(problems)
        return True, (
            "Event 004 'Shadow Pact' created as a SECRET event targeting you. "
            "Check your DMs for the secret mission message."
        )

    steps.append({
        "title": "setevent (secret event)",
        "command": "setevent",
        "instruction": (
            "Run:\n`/setevent name:Shadow Pact description:A hooded figure offers you a clandestine job. "
            f"stat:ARC threshold:4 reward_type:COINS reward_amount:100 event_type:SECRET secret_targets:{gm.mention}`"
        ),
        "verify": verify_setevent_secret,
    })

    # 9. /servergames signup -----------------------------------------------------
    async def verify_signup():
        p = await poll_until(player)
        if not p:
            return False, "You don't appear to be signed up for the season yet."
        if player_role and isinstance(gm, discord.Member):
            if not any(r.id == player_role.id for r in gm.roles):
                return False, f"Signed up as {p['class_name']}, but the player role wasn't assigned."
        return True, (
            f"Signed up as a {p['class_name']} "
            f"(STR {p['str_stat']} / INT {p['int_stat']} / ARC {p['arc_stat']})."
        )

    steps.append({
        "title": "servergames signup",
        "command": "servergames signup",
        "instruction": "Run `/servergames signup` and pick any class from the dropdown.",
        "verify": verify_signup,
    })

    # 10. /showevents — before reaching map position ----------------------------
    async def verify_showevents_before():
        s = season()
        p = player()
        ev3 = db.get_event_by_code("003", s["id"], active_only=False)
        if not ev3 or not p:
            return False, "Couldn't load event 003 or your player record to check against."
        hidden = p["map_position"] < (ev3["map_position_required"] or 0)
        note = (
            "Event 003 'Hidden Grove' should NOT have appeared in the list (your Map Position is "
            f"{p['map_position']}, it requires {ev3['map_position_required']})."
            if hidden else
            "Note: your Map Position already meets the requirement, so 'Hidden Grove' may have appeared too."
        )
        return True, f"Command ran. {note} Confirm visually that the list matched."

    steps.append({
        "title": "showevents (before reaching map position)",
        "command": "showevents",
        "instruction": "Run `/showevents` and review the list of events shown.",
        "verify": verify_showevents_before,
    })

    # 11-12. /stat add — STR and MAP_POSITION -----------------------------------
    steps.append(make_stat_change_step(
        "stat add (STR adjustment)", "stat add",
        f"Run:\n`/stat add user:{gm.mention} stat:STR amount:2`",
        "str_stat", 2, "STR", gm, season, player,
    ))
    steps.append(make_stat_change_step(
        "stat add (MAP_POSITION adjustment)", "stat add",
        f"Run:\n`/stat add user:{gm.mention} stat:MAP_POSITION amount:1`",
        "map_position", 1, "Map Position", gm, season, player,
    ))

    # 13. /showevents — after reaching map position ------------------------------
    async def verify_showevents_after():
        s = season()
        p = player()
        ev3 = db.get_event_by_code("003", s["id"], active_only=False)
        if not ev3 or not p:
            return False, "Couldn't load event 003 or your player record to check against."
        visible = p["map_position"] >= (ev3["map_position_required"] or 0)
        if visible:
            return True, (
                f"Event 003 'Hidden Grove' should now appear (your Map Position {p['map_position']} "
                f"meets its requirement of {ev3['map_position_required']}). Confirm it showed up."
            )
        return False, (
            f"Your Map Position ({p['map_position']}) still doesn't meet Hidden Grove's "
            f"requirement ({ev3['map_position_required']}) — check the earlier stat add steps."
        )

    steps.append({
        "title": "showevents (after reaching map position)",
        "command": "showevents",
        "instruction": "Run `/showevents` again — 'Hidden Grove' should now be visible in the list.",
        "verify": verify_showevents_after,
    })

    # 14-15. /stat add — INVENTORY add/remove ------------------------------------
    async def verify_inv_add():
        s = season()
        p = await poll_until(lambda: db.get_player(str(gm.id), s["id"]))
        has_item = await poll_until(
            lambda: any(i["name"] == "Lucky Coin" for i in db.get_player_inventory(p["id"])) or None
        )
        if not has_item:
            return False, "'Lucky Coin' isn't in your inventory."
        return True, "'Lucky Coin' added to your inventory."

    steps.append({
        "title": "stat add (INVENTORY add)",
        "command": "stat add",
        "instruction": f"Run:\n`/stat add user:{gm.mention} stat:INVENTORY inventory_action:ADD inventory_item:Lucky Coin`",
        "verify": verify_inv_add,
    })

    async def verify_inv_remove():
        s = season()
        p = db.get_player(str(gm.id), s["id"])
        gone = await poll_until(
            lambda: (not any(i["name"] == "Lucky Coin" for i in db.get_player_inventory(p["id"]))) or None
        )
        if not gone:
            return False, "'Lucky Coin' is still in your inventory."
        return True, "'Lucky Coin' removed from your inventory."

    steps.append({
        "title": "stat add (INVENTORY remove)",
        "command": "stat add",
        "instruction": f"Run:\n`/stat add user:{gm.mention} stat:INVENTORY inventory_action:REMOVE inventory_item:Lucky Coin`",
        "verify": verify_inv_remove,
    })

    # 16. /editevent --------------------------------------------------------------
    threshold_snapshot: dict = {}

    def pre_editevent():
        s = season()
        ev = db.get_event_by_code("001", s["id"], active_only=False)
        threshold_snapshot["before"] = _row_get(ev, "threshold")

    async def verify_editevent():
        s = season()
        new_val = await poll_until_change(
            lambda: _row_get(db.get_event_by_code("001", s["id"], active_only=False), "threshold"),
            threshold_snapshot.get("before"),
        )
        if new_val is None:
            return False, "Event 001's threshold didn't change."
        if new_val != 2:
            return False, f"Threshold is now {new_val}, expected 2."
        return True, f"Event 001 threshold updated from {threshold_snapshot['before']} to {new_val}, and re-announced."

    steps.append({
        "title": "editevent",
        "command": "editevent",
        "pre": pre_editevent,
        "instruction": "Run:\n`/editevent event_id:001 threshold:2`",
        "verify": verify_editevent,
    })

    # 17. /announce ---------------------------------------------------------------
    steps.append({
        "title": "announce",
        "command": "announce",
        "instruction": (
            "Run:\n`/announce message:This is a test announcement from the guided test.`\n"
            f"Confirm it appears in {ann.mention}."
        ),
        "verify": lambda: _ok("Announcement command completed — confirm the message appeared in the announcement channel."),
    })

    # 18-20. /roll — public, map-gated, secret ------------------------------------
    async def verify_roll(code, label, dm_note=""):
        s = season()
        p = player()
        ev = db.get_event_by_code(code, s["id"])
        if not ev or not p:
            return False, f"Event {code} isn't active or your player record is missing — can't verify the roll."
        rolled = await poll_until(lambda: db.has_player_rolled(p["id"], ev["id"]) or None)
        if not rolled:
            return False, f"No roll recorded for event {code}."
        return True, f"Roll recorded for {label}.{dm_note}"

    steps.append({
        "title": "roll (public event)",
        "command": "roll",
        "instruction": (
            "Run `/roll event_id:001`. Whether you pass or fail, confirm the matching "
            "success/fail message appears in the result."
        ),
        "verify": lambda: verify_roll("001", "the public event 'Goblin Ambush'"),
    })
    steps.append({
        "title": "roll (map-gated event)",
        "command": "roll",
        "instruction": "Run `/roll event_id:003`.",
        "verify": lambda: verify_roll("003", "the map-gated event 'Hidden Grove'"),
    })
    steps.append({
        "title": "roll (secret event)",
        "command": "roll",
        "instruction": "Run `/roll event_id:004`. The result will be sent to your DMs.",
        "verify": lambda: verify_roll("004", "the secret event 'Shadow Pact'", " Check your DMs for the result."),
    })

    # 21-22. /stats — self and other ----------------------------------------------
    steps.append({
        "title": "stats (self)",
        "command": "stats",
        "instruction": "Run `/stats` to view your own stats.",
        "verify": lambda: _ok("Stats command completed — confirm your Class, stats, Map Position, Coins, and Inventory displayed correctly."),
    })
    steps.append({
        "title": "stats (viewing another player)",
        "command": "stats",
        "instruction": (
            f"Run `/stats user:{gm.mention}` to exercise the 'view another player' code path. "
            "(Since you're the only signed-up test player, this will render as your own view — "
            "the GM-only Coins/Inventory fields can't be fully exercised without a second account.)"
        ),
        "verify": lambda: _ok("Stats lookup completed for the specified user."),
    })

    # 23. /endevent ----------------------------------------------------------------
    async def verify_endevent():
        s = season()
        ev = await poll_until(
            lambda: db.get_event_by_code("002", s["id"], active_only=False)
            if not _row_get(db.get_event_by_code("002", s["id"], active_only=False), "is_active", 1)
            else None
        )
        if not ev:
            return False, "Event 002 is still active."
        return True, "Event 002 \"Dragon's Hoard\" deactivated."

    steps.append({
        "title": "endevent",
        "command": "endevent",
        "instruction": "Run `/endevent event_id:002`.",
        "verify": verify_endevent,
    })

    # 24. /removeitem ---------------------------------------------------------------
    async def verify_removeitem():
        s = season()
        gone = await poll_until(
            lambda: (not any(i["name"] == "Test Sword" for i in db.get_items(s["id"]))) or None,
            timeout=45,
        )
        if not gone:
            return False, "'Test Sword' is still in the item pool — did you click Confirm on the warning message?"
        return True, "'Test Sword' removed from the item pool (and cascaded from inventories/event rewards)."

    steps.append({
        "title": "removeitem",
        "command": "removeitem",
        "instruction": "Run `/removeitem item:Test Sword`, review the warning, then click **Confirm**.",
        "verify": verify_removeitem,
    })

    # 25-26. /servergames status / help ---------------------------------------------
    steps.append({
        "title": "servergames status",
        "command": "servergames status",
        "instruction": "Run `/servergames status`.",
        "verify": lambda: _ok("Status command completed — confirm it displayed the season number, start date, and signup count."),
    })
    steps.append({
        "title": "servergames help",
        "command": "servergames help",
        "instruction": "Run `/servergames help`.",
        "verify": lambda: _ok("Help command completed — confirm both the Gamemaster and Player command lists were posted."),
    })

    # 27. /servergames end -------------------------------------------------------------
    async def verify_end():
        result = await poll_until(lambda: True if not db.get_active_season() else None)
        if not result:
            return False, "A season is still marked active."
        return True, "Season ended — farewell announcement posted and season marked inactive."

    steps.append({
        "title": "servergames end",
        "command": "servergames end",
        "instruction": "Run `/servergames end` to wrap up the test season.",
        "verify": verify_end,
    })

    return steps


async def post_current_step(session: "TestSession"):
    if session.step_index >= len(session.steps):
        await finish_test_session(session)
        return
    step = session.steps[session.step_index]
    if "pre" in step:
        try:
            step["pre"]()
        except Exception:
            pass
    await session.log_channel.send(
        f"**Step {session.step_index + 1}/{len(session.steps)} — `{step['title']}`**\n{step['instruction']}"
    )


async def advance_test_step(session: "TestSession"):
    step = session.steps[session.step_index]
    await asyncio.sleep(1.5)
    try:
        passed, detail = await step["verify"]()
    except Exception as exc:
        passed, detail = False, f"Verification raised an error: {exc}"
    session.results.append((step["title"], passed, detail))
    emoji = "✅" if passed else "❌"
    try:
        await session.log_channel.send(f"{emoji} **{step['title']}** — {detail}")
    except discord.HTTPException as e:
        print(f"[advance_test_step] Failed to send step result to log channel: {e}")
    session.step_index += 1
    try:
        await post_current_step(session)
    except Exception as e:
        print(f"[advance_test_step] Failed to post next step: {e}")


async def finish_test_session(session: "TestSession"):
    global active_test_session
    passed = sum(1 for _, ok, _ in session.results if ok)
    total = len(session.results)
    lines = [f"🧪 **TEST COMPLETE — {passed}/{total} steps passed**", ""]
    for name, ok, detail in session.results:
        emoji = "✅" if ok else "❌"
        lines.append(f"{emoji} `{name}` — {detail}")
    lines.append("")
    lines.append(
        "🧹 Cleaning up — wiping the test data now. Run /servergames start when you're ready "
        "to begin a real season."
    )
    text = "\n".join(lines)
    for chunk in chunk_text(text):
        try:
            await session.log_channel.send(chunk)
        except discord.HTTPException as e:
            print(f"[finish_test_session] Failed to send summary chunk: {e}")

    db.wipe_all()
    try:
        await session.log_channel.send(
            "✅ Database wiped clean. All test data (season, players, items, events, rolls) has been removed."
        )
    except discord.HTTPException:
        pass

    active_test_session = None


class TestConfirmView(discord.ui.View):
    def __init__(self, actor: discord.Member):
        super().__init__(timeout=60)
        self.actor = actor
        self.confirmed = False
        self.original_interaction: discord.Interaction | None = None

    async def on_timeout(self):
        if self.original_interaction:
            try:
                await self.original_interaction.edit_original_response(
                    content="❌ Test run timed out before confirmation. Run /servergames test again to retry.",
                    view=None,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Wipe & Start Test", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.actor.id:
                await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
                return
            self.confirmed = True
            await interaction.response.edit_message(
                content="✅ Confirmed. Wiping the database and starting the guided test...", view=None
            )
            self.stop()
        except Exception as e:
            print(f"[TestConfirmView.confirm] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong starting the test. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.actor.id:
                await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
                return
            await interaction.response.edit_message(content="❌ Test run cancelled.", view=None)
            self.stop()
        except Exception as e:
            print(f"[TestConfirmView.cancel] Unhandled error: {e}")


@servergames.command(name="start", description="Start a new Server Games season")
@app_commands.describe(
    season_number="The season number to start",
    announcement_channel="Channel for public announcements",
    log_channel="Channel for Gamemaster logs",
    gamemaster_role="Gamemaster role (optional)",
    player_role="Server Games player role assigned on signup (optional)",
)
async def servergames_start(
    interaction: discord.Interaction,
    season_number: app_commands.Range[int, 1, 9999],
    announcement_channel: discord.TextChannel,
    log_channel: discord.TextChannel,
    gamemaster_role: discord.Role = None,
    player_role: discord.Role = None,
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
    db.configure_season(
        season["id"],
        str(announcement_channel.id),
        str(log_channel.id),
        str(gamemaster_role.id) if gamemaster_role else None,
        str(player_role.id) if player_role else None,
    )
    season = db.get_active_season()

    confirm_lines = [
        f"✅ Server Games Season {season_number} has been started.",
        f"Announcement Channel: {announcement_channel.mention}",
        f"Log Channel: {log_channel.mention}",
    ]
    if gamemaster_role:
        confirm_lines.append(f"Gamemaster Role: {gamemaster_role.mention}")
    if player_role:
        confirm_lines.append(f"Server Games Role: {player_role.mention}")

    await interaction.response.send_message("\n".join(confirm_lines), ephemeral=True)

    await send_announcement(
        season,
        (
            f"📣 SERVER GAMES — SEASON {season_number} HAS BEGUN!\n\n"
            "The new season is now live. Sign up using /servergames signup "
            "and choose your class.\nGood luck to all players."
        ),
    )

    log_details = f"Season {season_number} is now active.\n"
    log_details += f"Announcement channel: {announcement_channel.mention}\n"
    log_details += f"Log channel: {log_channel.mention}"
    if gamemaster_role:
        log_details += f"\nGamemaster role: {gamemaster_role.mention}"
    if player_role:
        log_details += f"\nServer Games role: {player_role.mention}"

    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/servergames start",
        log_details,
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
    def __init__(self):
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

    async def callback(self, interaction: discord.Interaction):
        try:
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
        except Exception as e:
            print(f"[ClassSelect.callback] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong processing your signup. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass


class ClassSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ClassSelect())


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
        embed=embed, view=ClassSelectView(), ephemeral=True
    )


@servergames.command(name="help", description="Show the Server Games command list")
async def servergames_help(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(HELP_TEXT_GM, ephemeral=True)
    await interaction.followup.send(HELP_TEXT_PLAYER, ephemeral=True)
    if interaction.message:
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass


@servergames.command(
    name="test",
    description="Wipe the database and run a guided, step-by-step test of every command",
)
async def servergames_test(interaction: discord.Interaction):
    global active_test_session

    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("❌ This command must be run in a server.", ephemeral=True)
        return

    if active_test_session is not None:
        await interaction.response.send_message(
            "❌ A test run is already in progress in the log channel. "
            "Finish it first, or restart the bot to cancel it.",
            ephemeral=True,
        )
        return

    season = db.get_active_season()
    if not season or not season["log_channel_id"] or not season["announcement_channel_id"]:
        await interaction.response.send_message(
            "❌ You need an active, configured season (announcement + log channels) before running "
            "the test — use /servergames start or /servergames configure first.",
            ephemeral=True,
        )
        return

    log_channel = bot.get_channel(int(season["log_channel_id"]))
    announcement_channel = bot.get_channel(int(season["announcement_channel_id"]))
    if log_channel is None or announcement_channel is None:
        await interaction.response.send_message(
            "❌ Could not resolve the configured announcement/log channels.", ephemeral=True
        )
        return

    gm_role = (
        interaction.guild.get_role(int(season["gamemaster_role_id"]))
        if season["gamemaster_role_id"] else None
    )
    player_role = (
        interaction.guild.get_role(int(season["player_role_id"]))
        if season["player_role_id"] else None
    )

    view = TestConfirmView(interaction.user)
    await interaction.response.send_message(
        (
            "⚠️ **WARNING — /servergames test will:**\n"
            "• Permanently wipe ALL Server Games data (seasons, players, items, events, rolls, inventories)\n"
            f"• Post a guided, step-by-step walkthrough to {log_channel.mention} — "
            "you'll manually run each command, and I'll auto-detect and verify the result\n"
            "• Use **your account** as the test player (assigns you the player role, sends you DMs, "
            "adjusts your stats and inventory)\n\n"
            "Are you sure you want to proceed?"
        ),
        view=view,
        ephemeral=True,
    )
    view.original_interaction = interaction
    await view.wait()

    if not view.confirmed:
        return

    captured_log = log_channel
    captured_announcement = announcement_channel
    captured_gm_role = gm_role
    captured_player_role = player_role
    captured_gm = interaction.user

    db.wipe_all()
    season_number = db.get_next_season_number()

    active_test_session = TestSession(
        gm=captured_gm,
        log_channel=captured_log,
        announcement_channel=captured_announcement,
        gm_role=captured_gm_role,
        player_role=captured_player_role,
        season_number=season_number,
    )

    await captured_log.send(
        "🧪 **SERVER GAMES — GUIDED TEST STARTED**\n\n"
        f"Test player: {captured_gm.mention}\n"
        "The database has been wiped. Run the exact command shown for each step, in order — "
        "I'll automatically detect it, verify the result, and post the next step.\n"
    )
    await post_current_step(active_test_session)


stat_group = app_commands.Group(name="stat", description="Gamemaster stat management")


async def inventory_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    items = db.get_items(season["id"])
    return [
        app_commands.Choice(name=item["name"], value=str(item["id"]))
        for item in items
        if current.lower() in item["name"].lower()
    ][:25]


async def inventory_remove_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    target_id = interaction.namespace.user
    if not target_id:
        return []
    player = db.get_player(str(target_id.id) if hasattr(target_id, "id") else str(target_id), season["id"])
    if not player:
        return []
    items = db.get_player_inventory(player["id"])
    return [
        app_commands.Choice(name=item["name"], value=str(item["id"]))
        for item in items
        if current.lower() in item["name"].lower()
    ][:25]


@stat_group.command(name="add", description="Adjust a player's stats or inventory")
@app_commands.describe(
    user="Target player",
    stat="Stat to adjust",
    amount="Positive to add, negative to reduce (not used for INVENTORY)",
    inventory_action="ADD or REMOVE (only for INVENTORY stat)",
    inventory_item="Item to add/remove from inventory (only for INVENTORY stat)",
)
@app_commands.choices(
    stat=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
        app_commands.Choice(name="COINS", value="COINS"),
        app_commands.Choice(name="MAP_POSITION", value="MAP_POSITION"),
        app_commands.Choice(name="INVENTORY", value="INVENTORY"),
    ],
    inventory_action=[
        app_commands.Choice(name="ADD", value="ADD"),
        app_commands.Choice(name="REMOVE", value="REMOVE"),
    ],
)
@app_commands.autocomplete(inventory_item=inventory_item_autocomplete)
async def stat_add(
    interaction: discord.Interaction,
    user: discord.Member,
    stat: app_commands.Choice[str],
    amount: int = None,
    inventory_action: app_commands.Choice[str] = None,
    inventory_item: str = None,
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

    label = player_label(player)

    if stat.value == "INVENTORY":
        if not inventory_action or not inventory_item:
            await interaction.response.send_message(
                "❌ For INVENTORY, provide both inventory_action (ADD/REMOVE) and inventory_item.",
                ephemeral=True,
            )
            return
        try:
            item_obj = db.get_item_by_id(int(inventory_item))
        except (ValueError, TypeError):
            item_obj = None
        if not item_obj:
            await interaction.response.send_message("❌ Item not found.", ephemeral=True)
            return

        if inventory_action.value == "ADD":
            db.add_player_inventory(player["id"], item_obj["id"])
            await interaction.response.send_message(
                f"✅ Inventory updated.\nItem added to {label}'s inventory: {item_obj['name']}",
                ephemeral=True,
            )
            await send_log(
                season, "GAMEMASTER", interaction.user.mention, "/stat add",
                f"Target: {label} | Action: ADD item | Item: {item_obj['name']}",
            )
        else:
            player_inv = db.get_player_inventory(player["id"])
            has_item = any(i["id"] == item_obj["id"] for i in player_inv)
            if not has_item:
                await interaction.response.send_message(
                    f"❌ {label} does not have {item_obj['name']} in their inventory.",
                    ephemeral=True,
                )
                return
            db.remove_player_inventory(player["id"], item_obj["id"])
            await interaction.response.send_message(
                f"✅ Inventory updated.\nItem removed from {label}'s inventory: {item_obj['name']}",
                ephemeral=True,
            )
            await send_log(
                season, "GAMEMASTER", interaction.user.mention, "/stat add",
                f"Target: {label} | Action: REMOVE item | Item: {item_obj['name']}",
            )
        return

    if amount is None:
        await interaction.response.send_message(
            "❌ Amount is required for stat adjustments.", ephemeral=True
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
        f"Target: {label} | Stat: {stat_name} | Amount: {amount:+d} | New value: {new_value}",
    )


async def reward_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    items = db.get_items(season["id"])
    return [
        app_commands.Choice(name=item["name"], value=str(item["id"]))
        for item in items
        if current.lower() in item["name"].lower()
    ][:25]


@bot.tree.command(name="setevent", description="Create a new Server Games event")
@app_commands.describe(
    name="Event name",
    description="Event flavour text",
    stat="Stat checked during rolls",
    threshold="Number players must beat",
    reward_type="Reward granted on pass",
    reward_amount="How much is awarded on pass (not used for ITEM rewards)",
    reward_item="Item reward — select from season item pool (only for ITEM reward type)",
    event_type="Leave blank for public, select SECRET for secret event",
    secret_targets="Required for SECRET events: @mention roles or players, comma-separated",
    map_position_required="Minimum map position required to see and roll this event",
    reward_2_type="Optional second reward granted on pass",
    reward_2_amount="How much the optional second reward gives",
    fail_message="Optional message shown when a player fails",
    success_message="Optional message shown when a player passes",
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
        app_commands.Choice(name="ITEM", value="ITEM"),
    ],
    reward_2_type=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
        app_commands.Choice(name="COINS", value="COINS"),
        app_commands.Choice(name="MAP_POSITION", value="MAP_POSITION"),
    ],
    event_type=[
        app_commands.Choice(name="PUBLIC", value="PUBLIC"),
        app_commands.Choice(name="SECRET", value="SECRET"),
    ],
)
@app_commands.autocomplete(reward_item=reward_item_autocomplete)
async def setevent(
    interaction: discord.Interaction,
    name: str,
    description: str,
    stat: app_commands.Choice[str],
    threshold: app_commands.Range[int, 1, 100],
    reward_type: app_commands.Choice[str],
    reward_amount: app_commands.Range[int, 1, 9999] = None,
    reward_item: str = None,
    event_type: app_commands.Choice[str] = None,
    secret_targets: str = None,
    map_position_required: app_commands.Range[int, 1, 9999] = None,
    reward_2_type: app_commands.Choice[str] = None,
    reward_2_amount: app_commands.Range[int, 1, 9999] = None,
    fail_message: str = None,
    success_message: str = None,
):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command must be used in a server.", ephemeral=True
        )
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    is_secret = event_type is not None and event_type.value == "SECRET"

    # Validate ITEM reward
    item_obj = None
    if reward_type.value == "ITEM":
        if not reward_item:
            await interaction.response.send_message(
                "❌ Please select an item from the item pool as the reward.",
                ephemeral=True,
            )
            return
        try:
            item_obj = db.get_item_by_id(int(reward_item))
        except (ValueError, TypeError):
            item_obj = None
        if not item_obj:
            await interaction.response.send_message(
                "❌ Please select an item from the item pool as the reward.",
                ephemeral=True,
            )
            return
    elif reward_amount is None:
        await interaction.response.send_message(
            "❌ Reward Amount is required for non-ITEM reward types.", ephemeral=True
        )
        return

    # Validate secret event targets
    typed_targets: list[dict] = []
    if is_secret:
        if not secret_targets:
            await interaction.response.send_message(
                "❌ Secret events require at least one target role or player.",
                ephemeral=True,
            )
            return
        typed_targets = parse_targets_with_type(secret_targets)
        if not typed_targets:
            await interaction.response.send_message(
                "❌ Could not parse any valid role or player mentions from secret_targets.",
                ephemeral=True,
            )
            return

    if (reward_2_type is None) != (reward_2_amount is None):
        await interaction.response.send_message(
            "❌ Reward 2 needs both a type and an amount, or neither.", ephemeral=True
        )
        return

    cleaned_fail_message = fail_message.strip() if fail_message else None
    cleaned_success_message = success_message.strip() if success_message else None
    event_code = db.next_event_code(season["id"])
    db.create_event(
        season["id"],
        event_code,
        name,
        description,
        stat.value,
        threshold,
        reward_type.value,
        reward_amount,
        item_obj["id"] if item_obj else None,
        reward_2_type.value if reward_2_type else None,
        reward_2_amount,
        cleaned_fail_message,
        success_message=cleaned_success_message,
        is_secret=is_secret,
        secret_targets=[{"id": str(t["id"]), "type": t["type"]} for t in typed_targets] if is_secret else None,
        secret_target_type=None,
        map_position_required=map_position_required,
    )

    reward_text = reward_announcement_text(reward_type.value, reward_amount)
    reward_lines = [f"Reward: {reward_text}"]
    if reward_2_type:
        reward_2_text = reward_announcement_text(reward_2_type.value, reward_2_amount)
        reward_lines.append(f"Reward 2: {reward_2_text}")
    reward_block = "\n".join(reward_lines)

    reward_log = (
        f"Reward: {reward_type.value} | Detail: {item_obj['name'] if item_obj else f'+{reward_amount}'}"
    )
    if reward_2_type:
        reward_log += f" | Reward 2: {reward_2_type.value} +{reward_2_amount}"
    if cleaned_fail_message:
        reward_log += f'\nFail message: "{cleaned_fail_message}"'
    if cleaned_success_message:
        reward_log += f'\nSuccess message: "{cleaned_success_message}"'

    if not is_secret:
        await interaction.response.send_message(
            f"✅ Event created: {name} ({event_code})", ephemeral=True
        )
        if not map_position_required:
            await send_announcement(
                season,
                (
                    f"📣 NEW EVENT — {name}\n\n"
                    f"{description}\n\n"
                    f"Stat Check: {stat.value} | Threshold: {threshold}\n"
                    f"{reward_block}\n"
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
                f"Event created: {name} | ID: {event_code} | Type: PUBLIC\n"
                f"Stat: {stat.value} | Threshold: {threshold} | {reward_log}"
                + (f"\nMap Position Required: {map_position_required}" if map_position_required else "")
            ),
        )
    else:
        # Secret event — DM targeted players
        await interaction.response.defer(ephemeral=True)
        dm_success = 0
        dm_fail = 0
        dm_fail_names: list[str] = []

        unique_targets = resolve_target_members(
            db.get_event_by_code(event_code, season["id"]), interaction.guild
        )

        dm_msg = (
            f"🔒 SECRET MISSION — {name}\n\n"
            f"{description}\n\n"
            f"Stat Check: {stat.value} | Threshold: {threshold}\n"
            f"{reward_block}\n"
            f"Event ID: {event_code}\n\n"
            "Use /roll {event_code} to participate. Do not share this mission."
        ).replace("{event_code}", event_code)

        for member in unique_targets:
            target_player = db.get_player(str(member.id), season["id"])
            target_label = (
                player_label(target_player) if target_player else member.display_name
            )
            try:
                await member.send(dm_msg)
                dm_success += 1
            except discord.HTTPException:
                dm_fail += 1
                dm_fail_names.append(target_label)
                log_channel = (
                    bot.get_channel(int(season["log_channel_id"]))
                    if season["log_channel_id"]
                    else None
                )
                if log_channel:
                    try:
                        await log_channel.send(
                            f"⚠️ Could not DM {target_label} for secret event {event_code}. DMs may be disabled."
                        )
                    except discord.HTTPException:
                        pass

        await interaction.followup.send(
            f"✅ Secret event created: {name} ({event_code})\n"
            f"DMs sent: {dm_success} successful, {dm_fail} failed.",
            ephemeral=True,
        )
        target_display = ", ".join(f"{t['type']}:{t['id']}" for t in typed_targets)
        await send_log(
            season,
            "GAMEMASTER",
            interaction.user.mention,
            "/setevent",
            (
                f"Event created: {name} | ID: {event_code} | Type: SECRET\n"
                f"Stat: {stat.value} | Threshold: {threshold} | {reward_log}\n"
                f"Targets: {target_display}\n"
                f"DMs sent: {dm_success} successful, {dm_fail} failed"
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


RARITY_WEIGHTS = {"COMMON": 70, "RARE": 25, "EPIC": 5}


@bot.tree.command(name="additem", description="Add an item to the season item pool")
@app_commands.describe(
    name="Item name",
    description="Item description",
    scavengable="Make this item available in /scavenge",
    rarity="Scavenge drop rarity (required if scavengable)",
)
@app_commands.choices(
    rarity=[
        app_commands.Choice(name="Common (70%)", value="COMMON"),
        app_commands.Choice(name="Rare (25%)", value="RARE"),
        app_commands.Choice(name="Epic (5%)", value="EPIC"),
    ]
)
async def additem_cmd(
    interaction: discord.Interaction,
    name: str,
    description: str,
    scavengable: bool = False,
    rarity: app_commands.Choice[str] = None,
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

    if scavengable and not rarity:
        await interaction.response.send_message(
            "❌ Rarity is required when marking an item as scavengable.", ephemeral=True
        )
        return

    rarity_val = rarity.value if rarity else None
    db.create_item(season["id"], name.strip(), description.strip(), scavengable, rarity_val)

    scav_note = f"\nScavengable: Yes ({rarity_val})" if scavengable else ""
    await interaction.response.send_message(
        f"✅ Item added to the season item pool.\nName: {name}\nDescription: {description}{scav_note}",
        ephemeral=True,
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/additem",
        f'Item added: {name} — "{description}"' + (f" | Scavengable: {rarity_val}" if scavengable else ""),
    )


async def edititem_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    items = db.get_items(season["id"])
    return [
        app_commands.Choice(name=item["name"], value=str(item["id"]))
        for item in items
        if current.lower() in item["name"].lower()
    ][:25]


@bot.tree.command(name="edititem", description="Edit an item in the season item pool")
@app_commands.describe(
    item="Item to edit (autocomplete)",
    name="New name",
    description="New description",
    scavengable="Toggle scavengable status",
    rarity="New rarity (required when setting scavengable to Yes)",
)
@app_commands.choices(
    rarity=[
        app_commands.Choice(name="Common (70%)", value="COMMON"),
        app_commands.Choice(name="Rare (25%)", value="RARE"),
        app_commands.Choice(name="Epic (5%)", value="EPIC"),
    ]
)
@app_commands.autocomplete(item=edititem_autocomplete)
async def edititem_cmd(
    interaction: discord.Interaction,
    item: str,
    name: str = None,
    description: str = None,
    scavengable: bool = None,
    rarity: app_commands.Choice[str] = None,
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

    try:
        item_obj = db.get_item_by_id(int(item))
    except (ValueError, TypeError):
        item_obj = None
    if not item_obj or item_obj["season_id"] != season["id"]:
        await interaction.response.send_message("❌ Item not found.", ephemeral=True)
        return

    updates: dict = {}
    changes: list[str] = []

    if name is not None:
        updates["name"] = name.strip()
        changes.append(f"Name: → {name.strip()}")
    if description is not None:
        updates["description"] = description.strip()
        changes.append("Description updated")
    if scavengable is not None:
        if scavengable and not rarity:
            await interaction.response.send_message(
                "❌ Rarity is required when enabling scavengable.", ephemeral=True
            )
            return
        updates["scavengable"] = 1 if scavengable else 0
        if not scavengable:
            updates["rarity"] = None
            changes.append("Scavengable: → No (rarity cleared)")
        else:
            changes.append(f"Scavengable: → Yes")
    if rarity is not None:
        updates["rarity"] = rarity.value
        changes.append(f"Rarity: → {rarity.value}")

    if not updates:
        await interaction.response.send_message(
            "❌ No changes provided. Supply at least one field to edit.", ephemeral=True
        )
        return

    db.update_item_fields(item_obj["id"], updates)
    summary = "\n".join(changes)
    await interaction.response.send_message(
        f"✅ Item updated: {item_obj['name']}\n{summary}", ephemeral=True
    )
    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/edititem",
        f"Item edited: {item_obj['name']}\n{summary}",
    )


async def removeitem_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    items = db.get_items(season["id"])
    return [
        app_commands.Choice(name=item["name"], value=str(item["id"]))
        for item in items
        if current.lower() in item["name"].lower()
    ][:25]


class RemoveItemConfirmView(discord.ui.View):
    def __init__(self, item_id: int, item_name: str, season, actor_mention: str):
        super().__init__(timeout=60)
        self.item_id = item_id
        self.item_name = item_name
        self.season = season
        self.actor_mention = actor_mention
        self.original_interaction: discord.Interaction | None = None

    async def on_timeout(self):
        if self.original_interaction:
            try:
                await self.original_interaction.edit_original_response(
                    content="❌ Removal timed out. Run /removeitem again to retry.", view=None
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            affected_players = db.get_players_with_item(self.item_id)
            affected_events = db.get_events_with_item(self.item_id, self.season["id"])
            player_names = ", ".join(p["discord_username"] for p in affected_players) or "none"
            event_ids = ", ".join(e["event_id"] for e in affected_events) or "none"

            db.remove_item_cascade(self.item_id)
            await interaction.response.edit_message(
                content=(
                    f"✅ {self.item_name} has been removed from the item pool, "
                    "all player inventories, and all event rewards."
                ),
                view=None,
            )
            await send_log(
                self.season,
                "GAMEMASTER",
                self.actor_mention,
                "/removeitem",
                (
                    f"Item removed: {self.item_name}\n"
                    f"Affected players: {player_names}\n"
                    f"Affected events: {event_ids}"
                ),
            )
        except Exception as e:
            print(f"[RemoveItemConfirmView.confirm] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong removing the item. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.edit_message(content="❌ Removal cancelled.", view=None)
        except discord.HTTPException as e:
            print(f"[RemoveItemConfirmView.cancel] Unhandled error: {e}")


@bot.tree.command(name="removeitem", description="Remove an item from the season item pool")
@app_commands.describe(item="Item to remove")
@app_commands.autocomplete(item=removeitem_autocomplete)
async def removeitem_cmd(interaction: discord.Interaction, item: str):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    try:
        item_obj = db.get_item_by_id(int(item))
    except (ValueError, TypeError):
        item_obj = None
    if not item_obj:
        await interaction.response.send_message("❌ Item not found.", ephemeral=True)
        return

    affected_players = db.get_players_with_item(item_obj["id"])
    affected_events = db.get_events_with_item(item_obj["id"], season["id"])
    player_names = ", ".join(p["discord_username"] for p in affected_players)
    event_ids = ", ".join(e["event_id"] for e in affected_events)

    warning_lines = [f'⚠️ WARNING — Removing this item will:']
    if affected_players:
        warning_lines.append(
            f'• Remove "{item_obj["name"]}" from {len(affected_players)} player inventor'
            f'ies ({player_names})'
        )
    if affected_events:
        warning_lines.append(
            f'• Remove "{item_obj["name"]}" as the reward from {len(affected_events)} '
            f'active event(s) ({event_ids})'
        )
    if not affected_players and not affected_events:
        warning_lines.append(f'• Remove "{item_obj["name"]}" from the item pool (no players or events affected)')
    warning_lines.append("\nAre you sure?")

    view = RemoveItemConfirmView(item_obj["id"], item_obj["name"], season, interaction.user.mention)
    await interaction.response.send_message("\n".join(warning_lines), view=view, ephemeral=True)
    view.original_interaction = interaction


@bot.tree.command(name="showevents", description="Show all active Server Games events")
async def showevents_cmd(interaction: discord.Interaction):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    player = db.get_player(str(interaction.user.id), season["id"])
    if not player:
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return
    all_events = db.get_active_events(season["id"], include_secret=True)
    events = [
        e for e in all_events
        if (not e["is_secret"] or player_can_access_secret(interaction, e))
        and (not e["map_position_required"] or player["map_position"] >= e["map_position_required"])
    ]
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
        reward_lines = [f"Reward: {reward_text}"]
        if event["reward_2_type"] and event["reward_2_amount"]:
            reward_2_text = reward_announcement_text(
                event["reward_2_type"], event["reward_2_amount"]
            )
            reward_lines.append(f"Reward 2: {reward_2_text}")
        reward_block = "\n".join(reward_lines)

        secret_tag = " 🔒" if event["is_secret"] else ""
        map_tag = f" | Map Position ≥ {event['map_position_required']}" if event["map_position_required"] else ""
        embed.add_field(
            name=f"[{event['event_id']}] {event['name']}{secret_tag}",
            value=(
                f"{event['description']}\n"
                f"Stat: {event['stat']} | Threshold: {event['threshold']}{map_tag}\n"
                f"{reward_block}"
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


async def editevent_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    events = db.get_active_events(season["id"], include_secret=True)
    return [
        app_commands.Choice(
            name=f"{e['event_id']} — {e['name']}", value=e["event_id"]
        )
        for e in events
        if current.lower() in e["event_id"].lower() or current.lower() in e["name"].lower()
    ][:25]


@bot.tree.command(name="editevent", description="Edit an existing event's fields")
@app_commands.describe(
    event_id="Event ID to edit, e.g. 001",
    name="New event name",
    description="New event description",
    stat="New stat checked during rolls",
    threshold="New threshold players must beat",
    reward_type="New reward type",
    reward_amount="New reward amount",
    reward_item="New item reward — select from season item pool (only for ITEM reward type)",
    event_type="Change to PUBLIC or SECRET",
    secret_targets="@mention roles or players, comma-separated (only for SECRET)",
    map_position_required="Minimum map position required (0 to clear)",
    reward_2_type="New second reward type (use NONE to clear)",
    reward_2_amount="New second reward amount",
    fail_message="New fail message (use NONE to clear)",
    success_message="New success message (use NONE to clear)",
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
        app_commands.Choice(name="ITEM", value="ITEM"),
    ],
    reward_2_type=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
        app_commands.Choice(name="COINS", value="COINS"),
        app_commands.Choice(name="MAP_POSITION", value="MAP_POSITION"),
        app_commands.Choice(name="NONE (clear)", value="NONE"),
    ],
    event_type=[
        app_commands.Choice(name="PUBLIC", value="PUBLIC"),
        app_commands.Choice(name="SECRET", value="SECRET"),
    ],
)
@app_commands.autocomplete(event_id=editevent_id_autocomplete, reward_item=reward_item_autocomplete)
async def editevent_cmd(
    interaction: discord.Interaction,
    event_id: str,
    name: str = None,
    description: str = None,
    stat: app_commands.Choice[str] = None,
    threshold: app_commands.Range[int, 1, 100] = None,
    reward_type: app_commands.Choice[str] = None,
    reward_amount: app_commands.Range[int, 1, 9999] = None,
    reward_item: str = None,
    event_type: app_commands.Choice[str] = None,
    secret_targets: str = None,
    map_position_required: int = None,
    reward_2_type: app_commands.Choice[str] = None,
    reward_2_amount: app_commands.Range[int, 1, 9999] = None,
    fail_message: str = None,
    success_message: str = None,
):
    if not is_gamemaster(interaction):
        await interaction.response.send_message(PERMISSION_DENIED_MSG, ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command must be used in a server.", ephemeral=True
        )
        return

    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    normalized_id = db.normalize_event_code(event_id)
    event = db.get_event_by_code(event_id, season["id"], active_only=False)
    if not event:
        await interaction.response.send_message(
            f"❌ Event {normalized_id} not found.", ephemeral=True
        )
        return

    updates: dict = {}
    changes: list[str] = []

    if name is not None:
        updates["name"] = name.strip()
        changes.append(f"Name: {event['name']} → {name.strip()}")

    if description is not None:
        updates["description"] = description.strip()
        changes.append(f"Description updated")

    if stat is not None:
        updates["stat"] = stat.value
        changes.append(f"Stat: {event['stat']} → {stat.value}")

    if threshold is not None:
        updates["threshold"] = threshold
        changes.append(f"Threshold: {event['threshold']} → {threshold}")

    if reward_type is not None:
        if reward_type.value == "ITEM":
            if not reward_item:
                await interaction.response.send_message(
                    "❌ Please select an item from the item pool as the reward.",
                    ephemeral=True,
                )
                return
            try:
                item_obj = db.get_item_by_id(int(reward_item))
            except (ValueError, TypeError):
                item_obj = None
            if not item_obj:
                await interaction.response.send_message(
                    "❌ Please select an item from the item pool as the reward.",
                    ephemeral=True,
                )
                return
            updates["reward_type"] = "ITEM"
            updates["reward_amount"] = None
            updates["reward_item_id"] = item_obj["id"]
            changes.append(f"Reward: → ITEM ({item_obj['name']})")
        else:
            if reward_amount is None and event["reward_type"] == "ITEM":
                await interaction.response.send_message(
                    "❌ Please provide a reward_amount when changing from ITEM to a stat reward.",
                    ephemeral=True,
                )
                return
            updates["reward_type"] = reward_type.value
            updates["reward_item_id"] = None
            if reward_amount is not None:
                updates["reward_amount"] = reward_amount
            changes.append(f"Reward type: {event['reward_type']} → {reward_type.value}")
    elif reward_amount is not None:
        updates["reward_amount"] = reward_amount
        changes.append(f"Reward amount: {event['reward_amount']} → {reward_amount}")

    if reward_2_type is not None:
        if reward_2_type.value == "NONE":
            updates["reward_2_type"] = None
            updates["reward_2_amount"] = None
            changes.append("Reward 2: cleared")
        else:
            updates["reward_2_type"] = reward_2_type.value
            changes.append(f"Reward 2 type: → {reward_2_type.value}")

    if reward_2_amount is not None:
        updates["reward_2_amount"] = reward_2_amount
        changes.append(f"Reward 2 amount: → {reward_2_amount}")

    if fail_message is not None:
        if fail_message.strip().upper() == "NONE":
            updates["fail_message"] = None
            changes.append("Fail message: cleared")
        else:
            updates["fail_message"] = fail_message.strip()
            changes.append(f"Fail message updated")

    if success_message is not None:
        if success_message.strip().upper() == "NONE":
            updates["success_message"] = None
            changes.append("Success message: cleared")
        else:
            updates["success_message"] = success_message.strip()
            changes.append("Success message updated")

    if event_type is not None:
        is_secret = event_type.value == "SECRET"
        updates["is_secret"] = 1 if is_secret else 0
        if is_secret:
            if not secret_targets:
                await interaction.response.send_message(
                    "❌ Secret events require at least one target role or player.",
                    ephemeral=True,
                )
                return
            typed_targets = parse_targets_with_type(secret_targets)
            if not typed_targets:
                await interaction.response.send_message(
                    "❌ Could not parse any valid role or player mentions from secret_targets.",
                    ephemeral=True,
                )
                return
            updates["secret_targets"] = json.dumps([{"id": str(t["id"]), "type": t["type"]} for t in typed_targets])
            updates["secret_target_type"] = None
            target_display = ", ".join(t["type"] + ":" + str(t["id"]) for t in typed_targets)
            changes.append(f"Type: → SECRET (targets: {target_display})")
        else:
            updates["secret_targets"] = None
            updates["secret_target_type"] = None
            changes.append("Type: → PUBLIC")
    elif secret_targets is not None:
        typed_targets = parse_targets_with_type(secret_targets)
        if typed_targets:
            updates["secret_targets"] = json.dumps([{"id": str(t["id"]), "type": t["type"]} for t in typed_targets])
            target_display = ", ".join(t["type"] + ":" + str(t["id"]) for t in typed_targets)
            changes.append(f"Secret targets updated: {target_display}")

    if map_position_required is not None:
        if map_position_required == 0:
            updates["map_position_required"] = None
            changes.append("Map Position Required: cleared")
        else:
            updates["map_position_required"] = map_position_required
            changes.append(f"Map Position Required: → {map_position_required}")

    if not updates:
        await interaction.response.send_message(
            "❌ No fields provided to update.", ephemeral=True
        )
        return

    db.update_event_fields(event["id"], updates)
    updated_event = db.get_event_by_code(normalized_id, season["id"], active_only=False)

    changes_text = "\n".join(changes)

    # Build re-announcement text from the updated event
    reward_text = reward_announcement_text(updated_event["reward_type"], updated_event["reward_amount"])
    reward_lines = [f"Reward: {reward_text}"]
    if updated_event["reward_2_type"] and updated_event["reward_2_amount"]:
        reward_lines.append(f"Reward 2: {reward_announcement_text(updated_event['reward_2_type'], updated_event['reward_2_amount'])}")
    reward_block = "\n".join(reward_lines)

    if not updated_event["is_secret"]:
        await interaction.response.send_message(
            f"✅ Event {normalized_id} updated.\n{changes_text}", ephemeral=True
        )
        if not updated_event["map_position_required"]:
            await send_announcement(
                season,
                (
                    f"📣 UPDATED EVENT — {updated_event['name']}\n\n"
                    f"{updated_event['description']}\n\n"
                    f"Stat Check: {updated_event['stat']} | Threshold: {updated_event['threshold']}\n"
                    f"{reward_block}\n"
                    f"Event ID: {normalized_id}\n\n"
                    f"Use /roll {normalized_id} to participate!"
                ),
            )
    else:
        await interaction.response.defer(ephemeral=True)
        unique_targets = resolve_target_members(updated_event, interaction.guild)

        dm_msg = (
            f"🔒 SECRET MISSION UPDATE — {updated_event['name']}\n\n"
            f"{updated_event['description']}\n\n"
            f"Stat Check: {updated_event['stat']} | Threshold: {updated_event['threshold']}\n"
            f"{reward_block}\n"
            f"Event ID: {normalized_id}\n\n"
            f"Use /roll {normalized_id} to participate. Do not share this mission."
        )

        dm_success = 0
        dm_fail = 0
        for member in unique_targets:
            try:
                await member.send(dm_msg)
                dm_success += 1
            except discord.HTTPException:
                dm_fail += 1
                log_channel = (
                    bot.get_channel(int(season["log_channel_id"]))
                    if season["log_channel_id"]
                    else None
                )
                if log_channel:
                    target_player = db.get_player(str(member.id), season["id"])
                    target_label = player_label(target_player) if target_player else member.display_name
                    try:
                        await log_channel.send(
                            f"⚠️ Could not DM {target_label} for secret event update {normalized_id}. DMs may be disabled."
                        )
                    except discord.HTTPException:
                        pass

        await interaction.followup.send(
            f"✅ Event {normalized_id} updated. DMs sent: {dm_success} successful, {dm_fail} failed.\n{changes_text}",
            ephemeral=True,
        )

    await send_log(
        season,
        "GAMEMASTER",
        interaction.user.mention,
        "/editevent",
        f"Event edited: {updated_event['name']} | ID: {normalized_id}\n{changes_text}",
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

    # Secret event access check — deny if player not in target list
    if event["is_secret"] and not player_can_access_secret(interaction, event):
        await interaction.response.send_message(
            f"❌ Event {normalized_id} not found.", ephemeral=True
        )
        return

    # Map position gate
    if event["map_position_required"] and player["map_position"] < event["map_position_required"]:
        await interaction.response.send_message(
            f"❌ You need to be at Map Position {event['map_position_required']} to participate in this event.",
            ephemeral=True,
        )
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
    header = (
        f"🔒 SECRET MISSION RESULT — {event['name']}"
        if event["is_secret"]
        else f"🎲 SERVER GAMES — {event['name']}"
    )
    lines = [
        header,
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
        if event["reward_type"] == "ITEM":
            item_obj = db.get_item_by_id(event["reward_item_id"]) if event["reward_item_id"] else None
            if item_obj:
                db.add_player_inventory(player["id"], item_obj["id"])
                if event["is_secret"]:
                    lines.append(f'🏆 Item Received: {item_obj["name"]}')
                    lines.append(f'"{item_obj["description"]}"')
                else:
                    lines.append("🏆 Reward: Item awarded")
                log_details += f'\nReward: ITEM | Detail: {item_obj["name"]} — "{item_obj["description"]}"'
            else:
                lines.append("🏆 Reward: Item awarded")
                log_details += "\nReward: ITEM | Detail: item no longer exists in pool (was removed)"
        else:
            current_player = player
            for reward_type, reward_amount in event_rewards(event):
                _, old_value, new_value = apply_reward(
                    current_player, reward_type, reward_amount
                )
                log_details = append_reward_result(
                    lines,
                    log_details,
                    label,
                    reward_type,
                    reward_amount,
                    old_value,
                    new_value,
                )
                current_player = db.get_player(str(interaction.user.id), season["id"])
        if event["success_message"]:
            lines.append(event["success_message"])
            log_details += f"\nSuccess message shown: {event['success_message']}"
    elif event["fail_message"]:
        lines.append(event["fail_message"])
        log_details += f"\nFail message shown: {event['fail_message']}"

    secret_tag = " [SECRET]" if event["is_secret"] else ""
    if event["is_secret"]:
        await interaction.response.send_message(
            "🔒 Result sent to your DMs.", ephemeral=True
        )
        try:
            await interaction.user.send("\n".join(lines))
        except discord.HTTPException:
            await interaction.followup.send(
                "⚠️ Could not send you a DM. Please enable DMs from server members.",
                ephemeral=True,
            )
    else:
        await interaction.response.send_message("\n".join(lines))

    await send_log(
        season,
        "PLAYER",
        label,
        f"/roll {event['event_id']}{secret_tag}",
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

    def inventory_block(player_id: int) -> str:
        inv = db.get_player_inventory(player_id)
        if not inv:
            return "📦 INVENTORY — Empty"
        lines = ["📦 INVENTORY"]
        for item in inv:
            lines.append(f'• {item["name"]} — {item["description"]}')
        return "\n".join(lines)

    if viewing_self:
        text = (
            f"📊 YOUR STATS — Season {season['season_number']}\n\n"
            f"Class: {player['class_name']}\n"
            f"Username: {player['discord_username']}\n\n"
            f"STR: {player['str_stat']}\n"
            f"INT: {player['int_stat']}\n"
            f"ARC: {player['arc_stat']}\n\n"
            f"Map Position: {player['map_position']}\n"
            f"Coins: {player['coins']}\n\n"
            + inventory_block(player["id"])
        )
    elif gamemaster_view:
        text = (
            f"📊 STATS — {label} [GM VIEW]\n\n"
            f"Class: {player['class_name']}\n\n"
            f"STR: {player['str_stat']}\n"
            f"INT: {player['int_stat']}\n"
            f"ARC: {player['arc_stat']}\n\n"
            f"Map Position: {player['map_position']}\n"
            f"Coins: {player['coins']}\n\n"
            + inventory_block(player["id"])
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


# ── /trade ───────────────────────────────────────────────────────────────────

async def trade_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    season = db.get_active_season()
    if not season:
        return []
    player = db.get_player(str(interaction.user.id), season["id"])
    if not player:
        return []
    items = db.get_player_inventory(player["id"])
    return [
        app_commands.Choice(name=item["name"], value=str(item["id"]))
        for item in items
        if current.lower() in item["name"].lower()
    ][:25]


class TradeView(discord.ui.View):
    def __init__(self, seller: discord.Member, item_id: int, item_name: str, coins: int, season):
        super().__init__(timeout=300)  # 5 minutes
        self.seller = seller
        self.item_id = item_id
        self.item_name = item_name
        self.coins = coins
        self.season = season
        self.resolved = False
        self.listing_message: discord.Message | None = None

    async def _resolve(self):
        self.resolved = True
        self.stop()
        for child in self.children:
            child.disabled = True

    async def on_timeout(self):
        if self.resolved:
            return
        await self._resolve()
        if self.listing_message:
            try:
                await self.listing_message.edit(
                    content=(
                        f"~~💰 **TRADE LISTING** — {self.seller.display_name} was selling "
                        f"**{self.item_name}** for **{self.coins} coins**~~\n"
                        "_This listing has expired._"
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.success, emoji="🛒")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id == self.seller.id:
                await interaction.response.send_message(
                    "❌ You can't buy your own listing.", ephemeral=True
                )
                return
            if self.resolved:
                await interaction.response.send_message(
                    "❌ This listing is no longer available.", ephemeral=True
                )
                return

            season = db.get_active_season()
            if not season:
                await interaction.response.send_message(
                    "❌ No active season.", ephemeral=True
                )
                return

            buyer = db.get_player(str(interaction.user.id), season["id"])
            if not buyer:
                await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
                return

            seller_player = db.get_player(str(self.seller.id), season["id"])
            if not seller_player:
                await interaction.response.send_message(
                    "❌ The seller is no longer in the season.", ephemeral=True
                )
                return

            # Re-validate
            if buyer["coins"] < self.coins:
                await interaction.response.send_message(
                    f"❌ You need {self.coins} coins to buy this. You have {buyer['coins']}.",
                    ephemeral=True,
                )
                return
            if not any(i["id"] == self.item_id for i in db.get_player_inventory(seller_player["id"])):
                await interaction.response.send_message(
                    "❌ The seller no longer has that item.", ephemeral=True
                )
                return

            await self._resolve()

            buyer_label = player_label(buyer)
            seller_label_str = player_label(seller_player)

            db.update_player_field(buyer["id"], "coins", buyer["coins"] - self.coins)
            db.update_player_field(seller_player["id"], "coins", seller_player["coins"] + self.coins)
            db.remove_player_inventory(seller_player["id"], self.item_id)
            db.add_player_inventory(buyer["id"], self.item_id)

            try:
                await interaction.response.edit_message(
                    content=(
                        f"✅ **TRADE COMPLETE** — {buyer_label} bought **{self.item_name}** "
                        f"from {seller_label_str} for **{self.coins} coins**."
                    ),
                    view=self,
                )
            except discord.HTTPException as e:
                print(f"[TradeView.buy] Failed to edit listing message after trade: {e}")

            await send_log(
                season,
                "PLAYER",
                interaction.user.display_name,
                "/trade (buy)",
                (
                    f"Buyer: {buyer_label} | Seller: {seller_label_str}\n"
                    f"Item: {self.item_name} | Price: {self.coins} coins"
                ),
            )
        except Exception as e:
            print(f"[TradeView.buy] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong processing the trade. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cancel Listing", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.seller.id:
                await interaction.response.send_message(
                    "❌ Only the seller can cancel this listing.", ephemeral=True
                )
                return
            if self.resolved:
                await interaction.response.send_message(
                    "❌ This listing is already resolved.", ephemeral=True
                )
                return

            await self._resolve()
            try:
                await interaction.response.edit_message(
                    content=(
                        f"~~💰 **TRADE LISTING** — {self.seller.display_name} was selling "
                        f"**{self.item_name}** for **{self.coins} coins**~~\n"
                        "_Listing cancelled by seller._"
                    ),
                    view=self,
                )
            except discord.HTTPException as e:
                print(f"[TradeView.cancel] Failed to edit listing message: {e}")

            season = db.get_active_season()
            seller_player = db.get_player(str(self.seller.id), season["id"]) if season else None
            seller_label_str = player_label(seller_player) if seller_player else self.seller.display_name
            await send_log(
                season,
                "PLAYER",
                interaction.user.display_name,
                "/trade (cancel)",
                f"Seller {seller_label_str} cancelled listing of {self.item_name} for {self.coins} coins.",
            )
        except Exception as e:
            print(f"[TradeView.cancel] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong cancelling the listing. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass


@bot.tree.command(name="trade", description="List one of your items for sale")
@app_commands.describe(
    item="Item from your inventory to sell",
    coins="Asking price in coins (1–50)",
)
@app_commands.autocomplete(item=trade_item_autocomplete)
async def trade_cmd(
    interaction: discord.Interaction,
    item: str,
    coins: app_commands.Range[int, 1, 50],
):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    seller = db.get_player(str(interaction.user.id), season["id"])
    if not seller:
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return

    try:
        item_obj = db.get_item_by_id(int(item))
    except (ValueError, TypeError):
        item_obj = None
    if not item_obj:
        await interaction.response.send_message("❌ Item not found.", ephemeral=True)
        return

    inventory = db.get_player_inventory(seller["id"])
    if not any(i["id"] == item_obj["id"] for i in inventory):
        await interaction.response.send_message(
            "❌ You don't have that item in your inventory.", ephemeral=True
        )
        return

    ann_channel = bot.get_channel(int(season["announcement_channel_id"])) if season["announcement_channel_id"] else None
    if ann_channel is None:
        await interaction.response.send_message(
            "❌ Announcement channel not configured.", ephemeral=True
        )
        return

    view = TradeView(interaction.user, item_obj["id"], item_obj["name"], coins, season)
    seller_label_str = player_label(seller)
    listing_msg = await ann_channel.send(
        f"💰 **TRADE LISTING** — {seller_label_str} is selling **{item_obj['name']}** for **{coins} coins**!\n"
        f"_{item_obj['description']}_\n\nAny signed-up player can click **Buy** to purchase.",
        view=view,
    )
    view.listing_message = listing_msg

    await interaction.response.send_message(
        f"✅ Your listing for **{item_obj['name']}** ({coins} coins) has been posted.", ephemeral=True
    )
    await send_log(
        season,
        "PLAYER",
        interaction.user.display_name,
        "/trade",
        f"{seller_label_str} listed {item_obj['name']} for {coins} coins.",
    )


# ── /challenge ────────────────────────────────────────────────────────────────

class ChallengeView(discord.ui.View):
    def __init__(self, challenger: discord.Member, defender: discord.Member, stat: str, wager: int, season):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.defender = defender
        self.stat = stat
        self.wager = wager
        self.season = season
        self.resolved = False
        self.listing_message: discord.Message | None = None

    async def _resolve(self):
        self.resolved = True
        self.stop()
        for child in self.children:
            child.disabled = True

    async def on_timeout(self):
        if self.resolved:
            return
        await self._resolve()
        if self.listing_message:
            try:
                await self.listing_message.edit(
                    content=(
                        f"~~⚔️ **CHALLENGE** — {self.challenger.display_name} challenged "
                        f"{self.defender.display_name} to a {self.stat} duel for {self.wager} coins~~\n"
                        "_Challenge expired._"
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.defender.id:
                await interaction.response.send_message(
                    "❌ Only the challenged player can accept.", ephemeral=True
                )
                return
            if self.resolved:
                await interaction.response.send_message(
                    "❌ This challenge is no longer active.", ephemeral=True
                )
                return

            season = db.get_active_season()
            if not season:
                await interaction.response.send_message("❌ No active season.", ephemeral=True)
                return

            challenger_p = db.get_player(str(self.challenger.id), season["id"])
            defender_p = db.get_player(str(self.defender.id), season["id"])

            if not challenger_p or not defender_p:
                await interaction.response.send_message(
                    "❌ One of the players is no longer in the season.", ephemeral=True
                )
                return

            if challenger_p["coins"] < self.wager:
                await self._resolve()
                try:
                    await interaction.response.edit_message(
                        content=(
                            f"❌ **CHALLENGE CANCELLED** — {self.challenger.display_name} no longer has "
                            f"enough coins ({self.wager} required)."
                        ),
                        view=self,
                    )
                except discord.HTTPException as e:
                    print(f"[ChallengeView.accept] Failed to edit message: {e}")
                return

            if defender_p["coins"] < self.wager:
                await self._resolve()
                try:
                    await interaction.response.edit_message(
                        content=(
                            f"❌ **CHALLENGE DECLINED** — {self.defender.display_name} doesn't have "
                            f"enough coins ({self.wager} required)."
                        ),
                        view=self,
                    )
                except discord.HTTPException as e:
                    print(f"[ChallengeView.accept] Failed to edit message: {e}")
                return

            await self._resolve()

            stat_field = STAT_FIELD_MAP[self.stat]
            c_d1, c_d2 = random.randint(1, 6), random.randint(1, 6)
            d_d1, d_d2 = random.randint(1, 6), random.randint(1, 6)
            c_stat = challenger_p[stat_field]
            d_stat = defender_p[stat_field]
            c_total = c_d1 + c_d2 + c_stat
            d_total = d_d1 + d_d2 + d_stat

            c_label = player_label(challenger_p)
            d_label = player_label(defender_p)

            if c_total > d_total:
                db.update_player_field(challenger_p["id"], "coins", challenger_p["coins"] + self.wager)
                db.update_player_field(defender_p["id"], "coins", defender_p["coins"] - self.wager)
                outcome = f"🏆 **{c_label}** wins **{self.wager} coins**!"
                log_outcome = f"Winner: {c_label} | +{self.wager} coins"
            elif d_total > c_total:
                db.update_player_field(defender_p["id"], "coins", defender_p["coins"] + self.wager)
                db.update_player_field(challenger_p["id"], "coins", challenger_p["coins"] - self.wager)
                outcome = f"🏆 **{d_label}** wins **{self.wager} coins**!"
                log_outcome = f"Winner: {d_label} | +{self.wager} coins"
            else:
                outcome = "🤝 **It's a draw!** No coins transfer."
                log_outcome = "Draw — no coins transferred"

            result_text = (
                f"⚔️ **CHALLENGE RESULT — {self.stat}** (Wager: {self.wager} coins)\n\n"
                f"**{c_label}:** {c_d1}+{c_d2}+{c_stat} = **{c_total}**\n"
                f"**{d_label}:** {d_d1}+{d_d2}+{d_stat} = **{d_total}**\n\n"
                f"{outcome}"
            )
            try:
                await interaction.response.edit_message(content=result_text, view=self)
            except discord.HTTPException as e:
                print(f"[ChallengeView.accept] Failed to edit result message: {e}")

            await send_log(
                season,
                "PLAYER",
                interaction.user.display_name,
                "/challenge (result)",
                (
                    f"Challenger: {c_label} | Defender: {d_label} | Stat: {self.stat} | Wager: {self.wager}\n"
                    f"{c_label}: {c_d1}+{c_d2}+{c_stat}={c_total} vs {d_label}: {d_d1}+{d_d2}+{d_stat}={d_total}\n"
                    f"{log_outcome}"
                ),
            )
        except Exception as e:
            print(f"[ChallengeView.accept] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong resolving the challenge. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✖️")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.user.id != self.defender.id:
                await interaction.response.send_message(
                    "❌ Only the challenged player can decline.", ephemeral=True
                )
                return
            if self.resolved:
                await interaction.response.send_message(
                    "❌ This challenge is no longer active.", ephemeral=True
                )
                return

            await self._resolve()
            try:
                await interaction.response.edit_message(
                    content=(
                        f"~~⚔️ **CHALLENGE** — {self.challenger.display_name} challenged "
                        f"{self.defender.display_name} to a {self.stat} duel for {self.wager} coins~~\n"
                        f"_Declined by {self.defender.display_name}._"
                    ),
                    view=self,
                )
            except discord.HTTPException as e:
                print(f"[ChallengeView.decline] Failed to edit message: {e}")

            season = db.get_active_season()
            challenger_p = db.get_player(str(self.challenger.id), season["id"]) if season else None
            defender_p = db.get_player(str(self.defender.id), season["id"]) if season else None
            c_label = player_label(challenger_p) if challenger_p else self.challenger.display_name
            d_label = player_label(defender_p) if defender_p else self.defender.display_name
            await send_log(
                season,
                "PLAYER",
                interaction.user.display_name,
                "/challenge (declined)",
                f"{d_label} declined {c_label}'s {self.stat} challenge for {self.wager} coins.",
            )
        except Exception as e:
            print(f"[ChallengeView.decline] Unhandled error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong declining the challenge. Please try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass


@bot.tree.command(name="challenge", description="Challenge another player to a stat duel")
@app_commands.describe(
    target="Player to challenge",
    stat="Stat to compete on",
    wager="Coins to wager (must have at least this much)",
)
@app_commands.choices(
    stat=[
        app_commands.Choice(name="STR", value="STR"),
        app_commands.Choice(name="INT", value="INT"),
        app_commands.Choice(name="ARC", value="ARC"),
    ]
)
async def challenge_cmd(
    interaction: discord.Interaction,
    target: discord.Member,
    stat: app_commands.Choice[str],
    wager: app_commands.Range[int, 1, 9999],
):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    if target.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ You can't challenge yourself.", ephemeral=True
        )
        return

    challenger = db.get_player(str(interaction.user.id), season["id"])
    if not challenger:
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return

    defender = db.get_player(str(target.id), season["id"])
    if not defender:
        await interaction.response.send_message(
            "❌ That player is not signed up for the current season.", ephemeral=True
        )
        return

    if challenger["coins"] < wager:
        await interaction.response.send_message(
            f"❌ You need at least {wager} coins to issue this challenge. You have {challenger['coins']}.",
            ephemeral=True,
        )
        return

    ann_channel = bot.get_channel(int(season["announcement_channel_id"])) if season["announcement_channel_id"] else None
    if ann_channel is None:
        await interaction.response.send_message(
            "❌ Announcement channel not configured.", ephemeral=True
        )
        return

    c_label = player_label(challenger)
    d_label = player_label(defender)
    view = ChallengeView(interaction.user, target, stat.value, wager, season)
    msg = await ann_channel.send(
        f"⚔️ **CHALLENGE ISSUED!**\n\n"
        f"**{c_label}** challenges **{d_label}** to a **{stat.value}** duel for **{wager} coins**!\n\n"
        f"{target.mention} — do you accept?",
        view=view,
    )
    view.listing_message = msg

    await interaction.response.send_message(
        f"✅ Challenge issued to {target.display_name}.", ephemeral=True
    )
    await send_log(
        season,
        "PLAYER",
        interaction.user.display_name,
        "/challenge",
        f"{c_label} challenged {d_label} | Stat: {stat.value} | Wager: {wager} coins",
    )


# ── /scavenge ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="scavenge", description="Search for loot — roll against a random stat check")
async def scavenge_cmd(interaction: discord.Interaction):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    player = db.get_player(str(interaction.user.id), season["id"])
    if not player:
        await interaction.response.send_message(NOT_SIGNED_UP_MSG, ephemeral=True)
        return

    # Cooldown check (1 hour)
    if player["last_scavenge_at"]:
        last = datetime.fromisoformat(player["last_scavenge_at"])
        elapsed = (datetime.utcnow() - last).total_seconds()
        if elapsed < 3600:
            remaining = int(3600 - elapsed)
            mins, secs = divmod(remaining, 60)
            await interaction.response.send_message(
                f"⏳ You've already scavenged recently. Try again in **{mins}m {secs}s**.",
                ephemeral=True,
            )
            return

    pool = db.get_scavengable_items(season["id"])
    if not pool:
        await interaction.response.send_message(
            "❌ There are no scavengable items in the season pool. Ask a Gamemaster to add some.",
            ephemeral=True,
        )
        return

    stat = random.choice(["STR", "INT", "ARC"])
    threshold = random.randint(1, 20)
    stat_value = get_player_stat_value(player, stat)
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    dice_sum = d1 + d2
    total = dice_sum + stat_value
    passed = total > threshold

    db.update_player_scavenge_time(player["id"], datetime.utcnow().isoformat())

    lines = [
        f"🔍 SCAVENGE — {stat} Check (Threshold: {threshold})",
        "",
        f"Dice Roll: {d1} + {d2} = {dice_sum}",
        f"{stat} Stat: +{stat_value}",
        f"Result Check: {dice_sum} + {stat_value} = {total} > {threshold}",
        "",
    ]

    if passed:
        weights = [RARITY_WEIGHTS.get(i["rarity"], 1) for i in pool]
        found = random.choices(pool, weights=weights, k=1)[0]
        db.add_player_inventory(player["id"], found["id"])
        rarity_tag = f" [{found['rarity']}]" if found["rarity"] else ""
        lines.append(f"✅ You found: **{found['name']}**{rarity_tag} — {found['description']}")
        log_detail = f"PASS | Item found: {found['name']} ({found['rarity']})"
    else:
        lines.append("❌ Nothing found this time.")
        log_detail = "FAIL | No item found"

    await interaction.response.send_message("\n".join(lines), ephemeral=True)
    await send_log(
        season,
        "PLAYER",
        interaction.user.display_name,
        "/scavenge",
        (
            f"{player_label(player)} | Stat: {stat} | Threshold: {threshold} | "
            f"Dice: {d1}+{d2}+{stat_value}={total} | {log_detail}"
        ),
    )


# ── /leaderboard ──────────────────────────────────────────────────────────────

@bot.tree.command(name="leaderboard", description="View the top players ranked by Map Position")
async def leaderboard_cmd(interaction: discord.Interaction):
    season = db.get_active_season()
    if not season:
        await interaction.response.send_message(
            "❌ There is no active Server Games season.", ephemeral=True
        )
        return

    ann_channel = bot.get_channel(int(season["announcement_channel_id"])) if season["announcement_channel_id"] else None
    if ann_channel is None:
        await interaction.response.send_message(
            "❌ Announcement channel not configured.", ephemeral=True
        )
        return

    players = db.get_leaderboard(season["id"], top_positions=5)
    if not players:
        await interaction.response.send_message(
            "❌ No players have signed up yet this season.", ephemeral=True
        )
        return

    lines = [f"🏆 **SERVER GAMES — LEADERBOARD** (Season {season['season_number']})", ""]
    current_pos = None
    for p in players:
        if p["map_position"] != current_pos:
            current_pos = p["map_position"]
            lines.append(f"**Map Position {current_pos}**")
        lines.append(f"• {player_label(p)}")

    await interaction.response.send_message("✅ Leaderboard posted.", ephemeral=True)
    await ann_channel.send("\n".join(lines))


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
        return

    await ctx.send(f"[{remaining}] more users need to arm the bomb.")


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    session = active_test_session
    if session is None or session.step_index >= len(session.steps):
        return
    if interaction.user.id != session.gm.id:
        return

    step = session.steps[session.step_index]
    expected = step["command"]
    qualified = getattr(command, "qualified_name", None)
    if qualified != expected:
        return

    try:
        await advance_test_step(session)
    except Exception as e:
        print(f"[on_app_command_completion] Error in advance_test_step: {e}")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    msg = "❌ Something went wrong. Please try again or contact a Gamemaster."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass
    raise error


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
