# Server Games Discord Bot — Full Requirements Document

## Overview

This document describes all commands, mechanics, and behaviours for a Discord bot called **Server Games**. The bot manages a seasonal RPG-style game played within a Discord server. Admins control the game flow. Players sign up, choose a class, and participate in events using a dice roll system. All data must persist in a database.

---

## Tech Stack Recommendation

- **Language:** JavaScript (Node.js) or Python — match whatever the existing bot is built in
- **Discord Library:** Discord.js (JS) or discord.py (Python)
- **Database:** SQLite (simple) or PostgreSQL (production)
- **Slash Commands:** All commands must be registered as Discord slash commands

---

## Database Schema

### Tables Required

**seasons**
- `id` — integer, primary key, auto increment
- `season_number` — integer, unique
- `is_active` — boolean
- `announcement_channel_id` — string
- `log_channel_id` — string
- `created_at` — timestamp

**players**
- `id` — integer, primary key
- `discord_id` — string, unique per season
- `discord_username` — string
- `season_id` — foreign key → seasons
- `class_name` — string (Hero / Mage / Bard / Rogue / Drakon)
- `str` — integer
- `int` — integer
- `arc` — integer
- `coins` — integer, default 0
- `map_position` — integer, default 1
- `created_at` — timestamp

**events**
- `id` — integer, primary key
- `event_id` — string, auto generated (format: EVT-XXXX)
- `season_id` — foreign key → seasons
- `name` — string
- `description` — string
- `stat` — string (STR / INT / ARC)
- `threshold` — integer
- `reward_type` — string (STR / INT / ARC / COINS / MAP_POSITION)
- `reward_amount` — integer
- `is_active` — boolean, default true
- `created_at` — timestamp

**rolls**
- `id` — integer, primary key
- `player_id` — foreign key → players
- `event_id` — foreign key → events
- `dice1` — integer
- `dice2` — integer
- `stat_modifier` — integer
- `total` — integer
- `threshold` — integer
- `result` — string (PASS / FAIL)
- `created_at` — timestamp

---

## Classes

Each class has 5 total base stat points distributed across STR (Strength), INT (Intelligence), and ARC (Arcana).

| Class | STR | INT | ARC | Description |
|-------|-----|-----|-----|-------------|
| Hero | 2 | 2 | 1 | The balanced warrior. Equally capable in any situation — not the best at anything, never the worst. |
| Mage | 1 | 3 | 1 | The scholar. Outthinks every obstacle but struggles when brute force is the only answer. |
| Bard | 1 | 1 | 3 | The wildcard. Thrives on chaos and luck — incredible highs, catastrophic lows. Not for the faint hearted. |
| Rogue | 2 | 1 | 2 | The phantom. Blends strength and cunning — unpredictable, adaptable, dangerous in the right hands. |
| Drakon | 3 | 1 | 1 | The destroyer. Unmatched in raw power but limited elsewhere. High risk, high reward. |

---

## Stat Roll Bonus

Used during dice rolls. The player's base stat in the relevant category is added directly to the dice result.

Example: if a player has STR 2 and rolls 4 + 4, the final result is `8 + 2 = 10`.

---

## Player Addressing Convention

All bot messages must address players as **[ClassName] [Username]**

Examples:
- `Drakon RedRiot`
- `Mage BlueFox`
- `Bard Stormwind`

This applies everywhere — announcements, roll results, stat updates, log entries.

---

## Privacy Rules

| Information | Player (own /stats) | Player (/stats @other) | Admin (/stats @other) |
|-------------|--------------------|-----------------------|----------------------|
| STR / INT / ARC | ✅ Visible | ✅ Visible | ✅ Visible |
| Map Position | ✅ Visible | ✅ Visible | ✅ Visible |
| Coins | ✅ Visible | ❌ Hidden | ✅ Visible |
| Coin reward amount in roll result | ❌ Hidden publicly | ❌ Hidden | ✅ Visible in log |

---

## Admin Commands

### `/servergames start`

**Permission:** Admin only

**Behaviour:**
1. Bot prompts admin for a season number (modal or option field)
2. Bot checks if any season is currently active in the database
3. If a season IS active → reply with error (ephemeral):
   ```
   ❌ Server Games Season [X] is already active.
   ```
4. If no season is active → create new season record in database, mark as active
5. Post in announcement channel:
   ```
   📣 SERVER GAMES — SEASON [X] HAS BEGUN!

   The new season is now live. Sign up using /servergames signup and choose your class.
   Good luck to all players.
   ```
6. Log to log channel:
   ```
   [timestamp] ADMIN — @username used /servergames start
   Season [X] is now active.
   ```

---

### `/servergames end`

**Permission:** Admin only

**Behaviour:**
1. Mark current active season as inactive in database
2. Post in announcement channel:
   ```
   📣 SERVER GAMES — SEASON [X] HAS ENDED.

   Thank you for participating!
   ```
3. Log to log channel:
   ```
   [timestamp] ADMIN — @username used /servergames end
   Season [X] has ended.
   ```

---

### `/servergames configure`

**Permission:** Admin only

**Behaviour:**
1. Prompt admin with two fields:
   - Announcement Channel (channel picker)
   - Log Channel (channel picker)
2. Save both channel IDs to the active season record in database
3. Reply (ephemeral):
   ```
   ✅ Configuration saved.
   Announcement Channel: #channel-name
   Log Channel: #channel-name
   ```
4. Log to log channel:
   ```
   [timestamp] ADMIN — @username used /servergames configure
   Announcement channel set: #channel-name
   Log channel set: #channel-name
   ```

---

### `/setevent`

**Permission:** Admin only

**Fields (all required):**
- `name` — string — event name
- `description` — string — flavour text
- `stat` — dropdown — STR / INT / ARC
- `threshold` — integer — the number players must beat
- `reward_type` — dropdown — STR / INT / ARC / COINS / MAP_POSITION
- `reward_amount` — integer — how much is awarded on pass

**Behaviour:**
1. Auto-generate a unique Event ID in format `EVT-XXXX` (e.g. EVT-0042)
2. Save event to database linked to active season
3. Post in announcement channel:
   ```
   📣 NEW EVENT — [Event Name]

   [Description]

   Stat Check: [STAT] | Threshold: [X]
   Reward: [reward description — see reward display rules below]
   Event ID: EVT-XXXX

   Use /roll EVT-XXXX to participate!
   ```
4. Log to log channel:
   ```
   [timestamp] ADMIN — @username used /setevent
   Event created: [Name] | ID: EVT-XXXX
   Stat: [STAT] | Threshold: [X] | Reward: [reward_type] +[reward_amount]
   ```

**Reward display rules in announcement:**
- If reward_type is COINS → display: `Coins`  (do NOT show amount publicly)
- If reward_type is STR/INT/ARC → display: `[STAT] +[amount]`
- If reward_type is MAP_POSITION → display: `Map Position +[amount]`

---

### `/announce`

**Permission:** Admin only

**Fields:**
- `message` — string

**Behaviour:**
1. Post the message in announcement channel, formatted as:
   ```
   📣 [message]
   ```
2. Log to log channel:
   ```
   [timestamp] ADMIN — @username used /announce
   Message: "[message]"
   ```

---

### `/stat add`

**Permission:** Admin only

**Fields:**
- `user` — @mention — target player
- `stat` — dropdown — STR / INT / ARC / COINS / MAP_POSITION
- `amount` — integer — positive to add, negative to reduce

**Behaviour:**
1. Look up the player in the database for the active season
2. Update the relevant stat by the given amount
3. Reply (ephemeral) confirming the change:
   ```
   ✅ Stats updated.
   Drakon RedRiot — STR: 3 → 4
   ```
4. Log to log channel:
   ```
   [timestamp] ADMIN — @username used /stat add
   Target: Drakon RedRiot | Stat: STR | Amount: +1 | New value: 4
   ```

**Notes:**
- Stats should not go below 0
- If stat would go below 0, reject with error: `❌ Stat cannot go below 0.`

---

### `/servergames help`

**Permission:** Admin only (also available to players — see player commands)

**Behaviour:** Ephemeral reply (only visible to the person who used the command)

```
📖 SERVER GAMES — COMMAND LIST

━━━━━━━━━━━━━━━━━━━━━━
🔐 ADMIN COMMANDS
━━━━━━━━━━━━━━━━━━━━━━

/servergames start
Start a new season. Bot will ask for the season
number before launching. Errors if season already active.

/servergames end
End the current season. Posts farewell message
in announcement channel.

/servergames configure
Set the announcement and log channels.

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

━━━━━━━━━━━━━━━━━━━━━━
🎮 PLAYER COMMANDS
━━━━━━━━━━━━━━━━━━━━━━

/servergames signup
Sign up for the current season. Bot shows class
descriptions and a dropdown to choose yours.

/roll [eventID]
Roll for an active event. 2 dice + your relevant
stat vs the event threshold. One roll per event.
Result posted publicly.

/stats
View your own stats — Class, STR, INT, ARC,
Map Position, Coins.

/stats @user
View another player's stats. Coins are hidden.

/servergames help
Show this command list.

━━━━━━━━━━━━━━━━━━━━━━
💡 Need help? Contact an admin.
━━━━━━━━━━━━━━━━━━━━━━
```

---

## Player Commands

### `/servergames signup`

**Availability:** Only when a season is active. If no season active, reply (ephemeral): `❌ There is no active Server Games season. Stay tuned!`

**If player already signed up this season**, reply (ephemeral): `❌ You are already signed up for Season [X] as [ClassName] [Username].`

**Behaviour:**
1. Bot sends an ephemeral embed to the player with all 5 classes and their descriptions:

```
⚔️ CHOOSE YOUR CLASS — SERVER GAMES SEASON [X]

Hero — STR 2 | INT 2 | ARC 1
The balanced warrior. Equally capable in any situation — not the best at anything, never the worst.

Mage — STR 1 | INT 3 | ARC 1
The scholar. Outthinks every obstacle but struggles when brute force is the only answer.

Bard — STR 1 | INT 1 | ARC 3
The wildcard. Thrives on chaos and luck — incredible highs, catastrophic lows. Not for the faint hearted.

Rogue — STR 2 | INT 1 | ARC 2
The phantom. Blends strength and cunning — unpredictable, adaptable, dangerous in the right hands.

Drakon — STR 3 | INT 1 | ARC 1
The destroyer. Unmatched in raw power but limited elsewhere. High risk, high reward.

[Dropdown: Choose your class ▼]
```

2. Player selects class from dropdown
3. Bot saves player to database with class preset stats
4. Bot posts publicly in announcement channel:
   ```
   ⚔️ Welcome to Server Games Season [X]
   Drakon RedRiot has joined the battle!
   ```
5. Log to log channel:
   ```
   [timestamp] PLAYER — Drakon RedRiot used /servergames signup
   Class selected: Drakon | STR: 3 | INT: 1 | ARC: 1
   ```

---

### `/roll [eventID]`

**Availability:** Only when a season is active and the specified event exists and is active.

**Validations (all ephemeral errors):**
- No active season → `❌ There is no active Server Games season.`
- Event ID not found → `❌ Event EVT-XXXX not found.`
- Player not signed up → `❌ You are not signed up for the current season. Use /servergames signup first.`
- Player already rolled this event → `❌ You have already participated in this event.`

**Behaviour:**
1. Roll 2 dice — each die is a random integer between 1 and 6
2. Get the event's stat type (STR / INT / ARC)
3. Get the player's base stat value for that type
4. Calculate total: `dice1 + dice2 + base stat`
5. Pass only if total is greater than the event threshold
6. Save roll record to database
7. Post result publicly in the channel:

**On PASS:**
```
🎲 SERVER GAMES — [Event Name]

Drakon RedRiot
Dice Roll: [dice1] + [dice2] = [dice_sum]
[STAT] Stat: +[base_stat]
Result Check: [dice_sum] + [base_stat] = [total] > [threshold]

✅ PASS
🏆 Reward: [reward display — see reward display rules]
Drakon RedRiot's [stat/position] has been updated: [old] → [new]
```

**On FAIL:**
```
🎲 SERVER GAMES — [Event Name]

Drakon RedRiot
Dice Roll: [dice1] + [dice2] = [dice_sum]
[STAT] Stat: +[base_stat]
Result Check: [dice_sum] + [base_stat] = [total] > [threshold]

❌ FAIL
```

**Reward display rules in public roll result:**
- STR / INT / ARC reward → show stat name, old value, new value
- MAP_POSITION reward → show old position, new position
- COINS reward → show only: `🏆 Reward: Coins awarded` (no amount, no balance)

**Auto update on PASS:**
- Immediately update the player's relevant stat/coins/map_position in the database

**Log to log channel:**
```
[timestamp] PLAYER — Drakon RedRiot used /roll EVT-XXXX
Dice: [d1]+[d2]=[sum] | [STAT] Stat: +[base_stat] | Total: [total]
Threshold: [threshold] | Result: PASS
Reward: [reward_type] +[reward_amount] | New value: [new_value]
```

For COINS specifically in the log:
```
Reward: COINS +[amount] | New balance: [new_balance]
```
*(Coin amounts ARE visible in the log channel)*

---

### `/stats`

**Behaviour:**
- If no argument → show the calling player's own stats (ephemeral)
- If `@user` provided → show that player's public stats (ephemeral)

**Own stats display:**
```
📊 YOUR STATS — Season [X]

Class: Drakon
Username: RedRiot

STR: 3
INT: 1
ARC: 1

Map Position: 2
Coins: 150
```

**Other player stats (non-admin):**
```
📊 STATS — Drakon RedRiot

Class: Drakon

STR: 3
INT: 1
ARC: 1

Map Position: 2
```

**Other player stats (admin):**
```
📊 STATS — Drakon RedRiot [ADMIN VIEW]

Class: Drakon

STR: 3
INT: 1
ARC: 1

Map Position: 2
Coins: 150
```

**Log to log channel:**
```
[timestamp] PLAYER — @username used /stats @RedRiot
```

---

### `/servergames help`

**Behaviour:** Ephemeral — same content as admin help command above. Show to any user who runs it.

---

## Log Channel — Full Logging Rules

Every bot action must be logged to the configured log channel. Format:

```
[HH:MM AM/PM] [TYPE] — [ClassName Username OR @adminUsername] used /[command]
[relevant details on next line(s)]
```

Types: `ADMIN` or `PLAYER`

**Everything that gets logged:**
- `/servergames start` — who started it, season number
- `/servergames end` — who ended it, season number
- `/servergames configure` — who configured, what channels were set
- `/setevent` — who created it, all event fields including reward amount
- `/announce` — who posted it, full message text
- `/stat add` — who ran it, target player, stat changed, old value, new value
- `/servergames signup` — player, class selected, starting stats
- `/roll` — player, event ID, both dice values, stat value, total, threshold, result, reward assigned including coin amounts
- `/stats @user` — who checked, whose stats were viewed
- `/servergames help` — who ran it

---

## Error Handling

All error messages must be **ephemeral** (only visible to the user who triggered them).

| Scenario | Error Message |
|----------|---------------|
| Start season when one is active | `❌ Server Games Season [X] is already active.` |
| Signup when no season active | `❌ There is no active Server Games season. Stay tuned!` |
| Signup when already signed up | `❌ You are already signed up for Season [X] as [ClassName] [Username].` |
| Roll when not signed up | `❌ You are not signed up for the current season. Use /servergames signup first.` |
| Roll on invalid event ID | `❌ Event [ID] not found.` |
| Roll when already participated | `❌ You have already participated in this event.` |
| Stat reduced below 0 | `❌ Stat cannot go below 0.` |
| Command used outside active season | `❌ There is no active Server Games season.` |

---

## Announcement Channel — What Gets Posted

| Trigger | Posts to Announcement Channel |
|---------|-------------------------------|
| `/servergames start` | Season start message |
| `/servergames end` | Season end / thank you message |
| `/setevent` | New event announcement with event ID |
| `/announce` | Admin's custom message |
| `/servergames signup` (player) | Welcome message with class and username |

---

## Implementation Notes for Cursor

1. **Check admin permissions** on every admin command using Discord role permissions — do not rely on a hardcoded user ID
2. **All slash commands** must be registered with Discord's application command API on bot startup
3. **Ephemeral replies** should be used for all errors, confirmations, and private info (own stats, help)
4. **Public replies** should be used for roll results and nothing else among player commands
5. **Dropdown menus** for class selection in `/servergames signup` should use Discord's SelectMenu component
6. **Event ID generation** — format `EVT-` followed by a zero-padded 4-digit number, auto-incrementing per season (EVT-0001, EVT-0002, etc.)
7. **Database lookups** for players should always be scoped to the active season — a player may sign up across multiple seasons
8. **Coin amounts** must never appear in any public-facing message — only in ephemeral own-stats view and the log channel
9. **[ClassName] [Username]** convention applies to ALL bot output messages without exception
