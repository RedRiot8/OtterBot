# Server Games Discord Bot Requirements

## Overview

This document reflects the bot as it is currently implemented in `bot.py` and `database.py`.

The bot runs a seasonal Discord RPG called **Server Games**. Gamemasters create and manage seasons, configure channels and roles, create events, adjust stats, and post announcements. Players sign up for an active season, choose a class, view events, roll for event outcomes, and view stats.

The bot also keeps the standalone `!bomb` prefix command.

---

## Runtime

- Language: Python
- Discord library: `discord.py`
- Database: SQLite
- Environment variable: `DISCORD_TOKEN`
- Slash commands are synced on bot startup.
- Prefix command support uses `!`.
- Discord intents enabled:
  - default intents
  - `message_content`, required for `!bomb`
  - `members`, required for role assignment and member lookups

---

## Files

- `bot.py` — Discord bot commands and game logic
- `database.py` — SQLite schema, migrations, and data access helpers
- `requirements.txt` — Python dependencies
- `servergames-bot-requirements.md` — this requirements document

Runtime/generated files such as `servergames.db`, `__pycache__`, and other generated files are not part of the bot source.

---

## Permissions

Gamemaster-only commands can be used by:

- Server administrators
- Users with Discord `manage_guild`
- Users who have the configured Gamemaster role for the active season

If a user lacks permission, the bot replies ephemerally:

```text
❌ You don't have the necessary permissions to execute this command.
```

Because the configured Gamemaster role is stored on the active season, `/servergames start` generally requires server administrator or `manage_guild` permission when no season is active.

---

## Database

Database file: `servergames.db`

### `seasons`

- `id` — integer primary key
- `season_number` — unique integer
- `is_active` — integer boolean
- `announcement_channel_id` — text, optional
- `log_channel_id` — text, optional
- `gamemaster_role_id` — text, optional
- `player_role_id` — text, optional
- `created_at` — text ISO timestamp

### `players`

- `id` — integer primary key
- `discord_id` — text
- `discord_username` — text
- `season_id` — integer foreign key
- `class_name` — text
- `str_stat` — integer
- `int_stat` — integer
- `arc_stat` — integer
- `coins` — integer, default `0`
- `map_position` — integer, default `1`
- `last_scavenge_at` — text ISO timestamp, nullable (tracks scavenge cooldown)
- `created_at` — text ISO timestamp

Constraint: `discord_id` and `season_id` are unique together.

### `items`

- `id` — integer primary key
- `season_id` — integer foreign key
- `name` — text
- `description` — text
- `created_at` — text ISO timestamp

Item pool is per season. All items are created fresh each season using `/additem`.

### `player_inventory`

- `id` — integer primary key
- `player_id` — integer foreign key
- `item_id` — integer foreign key
- `acquired_at` — text ISO timestamp

Tracks which items each player holds. A player can hold multiple copies of the same item.

### `events`

- `id` — integer primary key
- `event_id` — text, generated as a three-digit code such as `001`
- `season_id` — integer foreign key
- `name` — text
- `description` — text
- `stat` — text: `STR`, `INT`, or `ARC`
- `threshold` — integer
- `reward_type` — text: `STR`, `INT`, `ARC`, `COINS`, `MAP_POSITION`, or `ITEM`
- `reward_amount` — integer (null when reward_type is `ITEM`)
- `reward_item_id` — integer foreign key → `items` (null unless reward_type is `ITEM`)
- `reward_2_type` — optional text: `STR`, `INT`, `ARC`, `COINS`, or `MAP_POSITION`
- `reward_2_amount` — optional integer
- `fail_message` — optional text shown on a failed roll
- `success_message` — optional text shown on a passing roll
- `map_position_required` — optional integer; event is hidden until player's map_position >= this value
- `is_secret` — integer boolean, default `0`
- `secret_targets` — text JSON array of `{"id": "...", "type": "ROLE"|"PLAYER"}` objects (null if not secret)
- `is_active` — integer boolean, default `1`
- `created_at` — text ISO timestamp

Constraint: `event_id` and `season_id` are unique together.

### `rolls`

- `id` — integer primary key
- `player_id` — integer foreign key
- `event_id` — integer foreign key
- `dice1` — integer
- `dice2` — integer
- `stat_modifier` — integer (stores the player's raw stat value at roll time)
- `total` — integer
- `threshold` — integer
- `result` — text: `PASS` or `FAIL`
- `created_at` — text ISO timestamp

Constraint: `player_id` and `event_id` are unique together.

---

## Classes

Each player chooses one class during signup.

| Class | STR | INT | ARC | Description |
| --- | ---: | ---: | ---: | --- |
| Hero | 2 | 2 | 1 | The balanced warrior. Equally capable in any situation — not the best at anything, never the worst. |
| Mage | 1 | 3 | 1 | The scholar. Outthinks every obstacle but struggles when brute force is the only answer. |
| Bard | 1 | 1 | 3 | The wildcard. Thrives on chaos and luck — incredible highs, catastrophic lows. Not for the faint hearted. |
| Rogue | 2 | 1 | 2 | The phantom. Blends strength and cunning — unpredictable, adaptable, dangerous in the right hands. |
| Drakon | 3 | 1 | 1 | The destroyer. Unmatched in raw power but limited elsewhere. High risk, high reward. |

Player labels are formatted as `[ClassName] [Discord display name]`. Example: `Drakon RedRiot`. This convention applies to all bot messages — announcements, roll results, stat updates, and log entries.

---

## Rewards

Reward types: `STR`, `INT`, `ARC`, `COINS`, `MAP_POSITION`, `ITEM`

**Display rules (public messages and announcements):**

- `COINS` → displays only as `Coins` or `🏆 Reward: Coins awarded` — amount is never shown publicly
- `MAP_POSITION` → `Map Position +[amount]`
- `STR` / `INT` / `ARC` → `[STAT] +[amount]`
- `ITEM` → `Item awarded` — item name and description are never shown publicly

**Item visibility:**

- In a public roll result PASS: shows `🏆 Reward: Item awarded` only
- In a secret event DM PASS: shows item name and full description
- In own `/stats`: shows full inventory (name + description)
- In GM `/stats @user`: shows full inventory
- In another player's `/stats`: inventory is hidden

**Reward 2:**

Events can optionally have a second reward. Both `reward_2_type` and `reward_2_amount` must be provided together. On a passing roll, Reward 1 is applied first, then Reward 2.

---

## Roll Mechanics

Players roll two six-sided dice.

Final total:

```text
dice1 + dice2 + player's raw stat value
```

The checked stat comes from the event (`STR`, `INT`, or `ARC`).

A roll passes only if:

```text
total > threshold
```

Equal to the threshold is a fail.

Example:

```text
Player STR: 2
Dice: 4 + 4 = 8
Threshold: 7
Result check: 8 + 2 = 10 > 7 → PASS
```

---

## Privacy

| Information | Own `/stats` | Other player `/stats` | Gamemaster `/stats @user` | Public roll result | Log channel |
| --- | --- | --- | --- | --- | --- |
| STR / INT / ARC | visible | visible | visible | visible when relevant | visible |
| Map Position | visible | visible | visible | visible when rewarded | visible |
| Coins balance | visible | hidden | visible | hidden | visible |
| Coin reward amount | hidden | hidden | hidden | hidden | visible |
| Inventory items | visible | hidden | visible | hidden | visible |
| Item reward name | visible (own) | hidden | visible | hidden | visible |

---

## Logging

Logs are sent to the configured log channel.

Format:

```text
[HH:MM AM/PM] [TYPE] — [actor] used [command]
[details]
```

Types: `GAMEMASTER` or `PLAYER`

**Everything that gets logged:**

- `/servergames start` — who started it, season number
- `/servergames end` — who ended it, season number
- `/servergames configure` — channels and roles configured
- `/servergames signup` — player, class selected, starting stats
- `/setevent` — all event fields, reward detail, secret targets, DM delivery count
- `/editevent` — which fields changed and old → new values
- `/announce` — who posted it, full message text
- `/stat add` — target player, stat or inventory action, old → new value
- `/additem` — item name and description
- `/removeitem` — item name, affected players and events
- `/endevent` — event name and ID
- `/roll` — player, event ID, both dice, stat value, total, threshold, result, full reward detail
- `/stats @user` — who checked, whose stats were viewed

`/servergames status`, `/servergames help`, `/showevents`, and own `/stats` are not logged.

---

## Slash Commands

### `/servergames start`

Permission: Gamemaster only.

Arguments:
- `season_number` — integer from `1` to `9999` (required)
- `announcement_channel` — Discord text channel (required)
- `log_channel` — Discord text channel (required)
- `gamemaster_role` — optional Discord role
- `player_role` — optional Discord role assigned to players on signup

Validation:
- Season already active → `❌ Server Games Season [N] is already active.`
- Season number already exists → `❌ Season [N] already exists. Use a new season number (e.g. [next]).`

Behavior:
1. Creates the season record and saves all channel/role config in one step.
2. Replies ephemerally with confirmation listing configured channels and roles.
3. Immediately posts the season announcement to the configured announcement channel.
4. Logs the season start with all channel/role details.

Success response (ephemeral):
```text
✅ Server Games Season [N] has been started.
Announcement Channel: #[channel]
Log Channel: #[channel]
Gamemaster Role: @[role]
Server Games Role: @[role]
```

Announcement:
```text
📣 SERVER GAMES — SEASON [N] HAS BEGUN!

The new season is now live. Sign up using /servergames signup and choose your class.
Good luck to all players.
```

---

### `/servergames end`

Permission: Gamemaster only.

Validation: No active season → `❌ There is no active Server Games season.`

Success response (ephemeral): `✅ Server Games Season [N] has ended.`

Announcement:
```text
📣 SERVER GAMES — SEASON [N] HAS ENDED.

Thank you for participating!
```

---

### `/servergames status`

Available to all users.

Validation: No active season → `❌ There is no active Server Games season.`

Response (ephemeral):
```text
📣 SERVER GAMES — SEASON [N]

Started: [Month day, year]
Players signed up: [count]
```

---

### `/servergames configure`

Permission: Gamemaster only.

Arguments:
- `announcement_channel` — Discord text channel (required)
- `log_channel` — Discord text channel (required)
- `gamemaster_role` — optional Discord role
- `player_role` — optional Discord role assigned to players on signup

Behavior:
- Always saves announcement and log channels.
- Saves `gamemaster_role` only when provided — does not clear existing value when omitted.
- Saves `player_role` only when provided — does not clear existing value when omitted.

Success response (ephemeral):
```text
✅ Configuration saved.
Announcement Channel: #[channel]
Log Channel: #[channel]
Gamemaster Role: @[role]
Server Games Role: @[role]
```

Role lines are shown only when a role is configured.

---

### `/servergames signup`

Available to players when a season is active.

Validation:
- No active season → `❌ There is no active Server Games season. Stay tuned!`
- Already signed up → `❌ You are already signed up for Season [N] as [ClassName] [Username].`

Behavior:
1. Sends an ephemeral embed listing all 5 classes with stats and descriptions.
2. Shows a dropdown for class selection.
3. On selection: creates player record, assigns the configured player role (if set), sends ephemeral confirmation, posts announcement, logs signup.

Announcement:
```text
⚔️ Welcome to Server Games Season [N]
[ClassName] [Username] has joined the battle!
```

---

### `/servergames help`

Available to all users. Sends the full command list ephemerally.

---

### `/setevent`

Permission: Gamemaster only.

Required arguments:
- `name` — event name
- `description` — event description
- `stat` — `STR`, `INT`, or `ARC`
- `threshold` — integer from `1` to `100`
- `reward_type` — `STR`, `INT`, `ARC`, `COINS`, `MAP_POSITION`, or `ITEM`
- `reward_amount` — integer from `1` to `9999` (not required when reward_type is `ITEM`)

Optional arguments:
- `reward_item` — autocomplete from season item pool (required when reward_type is `ITEM`)
- `event_type` — `PUBLIC` (default) or `SECRET`
- `secret_target_type` — `ROLE` or `PLAYER` (required when event_type is `SECRET`)
- `secret_targets` — comma-separated Discord IDs of targeted roles or players (required when `SECRET`)
- `reward_2_type` — `STR`, `INT`, `ARC`, `COINS`, or `MAP_POSITION`
- `reward_2_amount` — integer from `1` to `9999`
- `fail_message` — text shown when a player fails

**Public event behavior:**
- Auto-generates event ID (`001`, `002`, etc.)
- Posts announcement
- Logs event details

**Secret event behavior:**
- Auto-generates event ID
- Does NOT post to announcement channel
- DMs each targeted player the event details
- If a DM fails, logs a warning to the log channel
- Logs DM success/fail counts

Announcement (public events only):
```text
📣 NEW EVENT — [name]

[description]

Stat Check: [STAT] | Threshold: [threshold]
Reward: [reward text]
Event ID: [event_code]

Use /roll [event_code] to participate!
```

Validations:
- `ITEM` reward with no item selected → `❌ Please select an item from the item pool as the reward.`
- `SECRET` with no targets → `❌ Secret events require at least one target role or player.`
- Only one Reward 2 field provided → `❌ Reward 2 needs both a type and an amount, or neither.`

---

### `/editevent`

Permission: Gamemaster only.

Required argument:
- `event_id` — event code to edit (autocomplete from active events)

Optional arguments (all same as `/setevent`):
- `name`, `description`, `stat`, `threshold`
- `reward_type`, `reward_amount`, `reward_item`
- `event_type`, `secret_target_type`, `secret_targets`
- `reward_2_type` (pass `NONE (clear)` to remove Reward 2), `reward_2_amount`
- `fail_message` (pass `NONE` to clear)

Behavior:
- Only fields that are explicitly provided are updated. Omitted fields remain unchanged.
- If changing `reward_type` to `ITEM`, `reward_item` must also be provided.
- If changing `event_type` to `SECRET`, `secret_target_type` and `secret_targets` must also be provided.
- Replies ephemerally with a summary of what changed.
- Logs all changes with old → new values.

---

### `/announce`

Permission: Gamemaster only.

Argument: `message` — text

Validation: No active season → `❌ There is no active Server Games season.`

Posts `📣 [message]` to the announcement channel.

---

### `/additem`

Permission: Gamemaster only.

Arguments:
- `name` — item name
- `description` — item description
- `scavengable` *(optional, default `False`)* — whether this item can appear in `/scavenge` results
- `rarity` *(optional, choices: `COMMON`, `RARE`, `EPIC`)* — required when `scavengable` is `True`; controls weighted drop rate in `/scavenge` (COMMON 70%, RARE 25%, EPIC 5%)

Behavior: Saves item to the season item pool. Items added here are available as rewards in `/setevent` and as options in `/stat add` (INVENTORY). If `scavengable` is `True`, the item also enters the scavenge pool with its rarity weight.

Success response (ephemeral):
```text
✅ Item added to the season item pool.
Name: [name]
Description: [description]
```

---

### `/edititem`

Permission: Gamemaster only.

Argument: `item` — autocomplete from season item pool

Optional fields (any combination):
- `name` — new item name
- `description` — new description
- `scavengable` — Yes / No toggle; setting No also clears the rarity field
- `rarity` — `COMMON`, `RARE`, or `EPIC`

Behavior: Updates the specified fields on the item. Responds with a summary of changes (ephemeral). If `scavengable` is set to No, `rarity` is cleared automatically.

---

### `/removeitem`

Permission: Gamemaster only.

Argument: `item` — autocomplete from season item pool

Behavior:
1. Checks which players currently hold the item and which active events use it as a reward.
2. Shows a warning with a Confirm / Cancel button (ephemeral).
3. On confirm: removes item from the `items` table, removes all matching `player_inventory` rows, and sets `reward_item_id = NULL` on any affected events (the `reward_type` field is left unchanged so events remain valid). All three actions happen atomically.

Warning message:
```text
⚠️ WARNING — Removing this item will:
• Remove "[name]" from [N] player inventories ([usernames])
• Remove "[name]" as the reward from [N] active event(s) ([event IDs])

Are you sure?
```

---

### `/stat add`

Permission: Gamemaster only.

Arguments:
- `user` — Discord member
- `stat` — `STR`, `INT`, `ARC`, `COINS`, `MAP_POSITION`, or `INVENTORY`
- `amount` — integer, positive or negative (not used when stat is `INVENTORY`)
- `inventory_action` — `ADD` or `REMOVE` (required when stat is `INVENTORY`)
- `inventory_item` — autocomplete from season item pool (required when stat is `INVENTORY`)

Validation:
- No active season → `❌ There is no active Server Games season.`
- Player not signed up → `❌ That player is not signed up for the current season.`
- New value below zero → `❌ Stat cannot go below 0.`

Success response for stat change (ephemeral):
```text
✅ Stats updated.
[ClassName] [Username] — [Stat]: [old] → [new]
```

Success response for inventory (ephemeral):
```text
✅ Inventory updated.
Item added to [ClassName] [Username]'s inventory: [item name]
```

---

### `/showevents`

Available to signed-up players.

Shows an ephemeral embed of all currently active **public** events. Secret events are never shown here.

Each event shows: event code and name, description, stat and threshold, reward, and Reward 2 when configured.

---

### `/endevent`

Permission: Gamemaster only.

Argument: `event_id` — event code, e.g. `001`

Sets the event's `is_active` to `0`. Works on both public and secret events.

---

### `/roll`

Available to signed-up players.

Argument: `event_id` — event code, e.g. `001`

Validations:
- No active season → `❌ There is no active Server Games season.`
- Event not found or inactive → `❌ Event [ID] not found.`
- Player not signed up → `❌ You must sign up for the current season using /servergames signup first.`
- Already rolled → `❌ You have already participated in this event.`
- Secret event — player not in target list → `❌ Event [ID] not found.` (does not reveal the event exists)

**Public event behavior:**

Result is posted publicly in the channel.

Pass result:
```text
🎲 SERVER GAMES — [event_name]

[ClassName] [Username]
Dice Roll: [d1] + [d2] = [sum]
[STAT] Stat: +[stat_value]
Result Check: [sum] + [stat_value] = [total] > [threshold]

✅ PASS
🏆 Reward: [reward text]
[ClassName] [Username]'s [STAT or Map Position] has been updated: [old] → [new]
```

For `ITEM` reward on pass (public): shows `🏆 Reward: Item awarded` only — no item name.

Fail result:
```text
🎲 SERVER GAMES — [event_name]

[ClassName] [Username]
Dice Roll: [d1] + [d2] = [sum]
[STAT] Stat: +[stat_value]
Result Check: [sum] + [stat_value] = [total] > [threshold]

❌ FAIL
[fail_message if configured]
```

**Secret event behavior:**

Bot replies ephemerally with `🔒 Result sent to your DMs.` and sends the full result via DM. Nothing is posted publicly.

For `ITEM` reward on secret event pass (DM):
```text
🏆 Item Received: [item name]
"[item description]"
```

**On pass:** Applies reward immediately — updates the relevant stat, coins, map_position, or inventory in the database.

---

### `/stats`

Available to signed-up players.

Optional argument: `user` — Discord member

Own stats response (ephemeral):
```text
📊 YOUR STATS — Season [N]

Class: [class]
Username: [username]

STR: [str]
INT: [int]
ARC: [arc]

Map Position: [map_position]
Coins: [coins]

📦 INVENTORY
• [item name] — [description]
```

Other player (non-Gamemaster) response (ephemeral):
```text
📊 STATS — [ClassName] [Username]

Class: [class]

STR: [str]
INT: [int]
ARC: [arc]

Map Position: [map_position]
```
(Coins and inventory hidden)

Gamemaster response (ephemeral):
```text
📊 STATS — [ClassName] [Username] [GAMEMASTER VIEW]

Class: [class]

STR: [str]
INT: [int]
ARC: [arc]

Map Position: [map_position]
Coins: [coins]

📦 INVENTORY
• [item name] — [description]
```

When a user views another player's stats, the lookup is logged.

---

## Prefix Commands

### `!bomb`

Not a slash command.

Behavior:
1. Tracks unique Discord user IDs that invoke `!bomb`.
2. On first activation: `Bomb has been activated!`
3. Each activation resets a 20-second countdown.
4. If 20 unique users invoke it: `💥 The server has exploded! 💥`
5. If countdown expires: `Bomb has been diffused.`
6. While waiting: `[remaining] more users need to arm the bomb.`

Bomb state is in memory only and resets on bot restart.

---

### `/trade`

Available to signed-up players.

**Direction:** the initiating player is always the **seller**. They list one of their own items at an asking coin price. Any other signed-up player with sufficient coins can buy it. There is no target — the listing is open to the whole server.

Arguments:
- `item` — autocomplete from the seller's own inventory
- `coins` — asking price, integer from `1` to `50`

**Pre-flight checks (before posting the listing):**
- Initiator is signed up for the active season.
- Initiator currently holds the specified item.

**Listing:** the bot posts a public message to the announcement channel with **Buy** and **Cancel Listing** buttons. The listing expires after 5 minutes.

**On Buy (any player except the seller):**
1. Validate buyer is signed up and has >= `coins`.
2. Re-validate seller still holds the item.
3. Deduct `coins` from buyer; add `coins` to seller.
4. Remove item from seller's inventory; add item to buyer's inventory.
5. Edit the listing message to show completion:
```text
✅ TRADE COMPLETE
[Buyer label] bought [item name] from [Seller label] for [coins] coins.
```
6. Log the full trade detail.

**On Cancel Listing (seller only):** edits listing to show cancelled; logged.

**Expiry (timeout):** the listing message is edited to show expired (strikethrough text); no action taken.

---

### `/challenge`

Available to signed-up players.

Arguments:
- `target` — Discord member (@mention)
- `stat` — `STR`, `INT`, or `ARC`
- `wager` — integer from `1` to the initiator's current coin balance

**Pre-flight checks:**
- Both players are signed up.
- Initiator holds at least `wager` coins.

**Request:** bot posts a public message pinging the target with **Accept** and **Decline** buttons. Only the target's clicks are valid. Expires after 60 seconds.

**On Accept:**
1. Re-validate both players hold >= `wager` coins.
2. Both players auto-roll 2d6; each total = dice1 + dice2 + their own value of the chosen stat.
3. Higher total wins. Winning player receives `wager` coins transferred from the losing player.
4. **Tie:** no coins transfer; result is a draw.
5. Post to announcement channel:
```text
⚔️ CHALLENGE RESULT — [stat]
[Challenger label]: [dice1]+[dice2]+[stat] = [total]
[Defender label]: [dice1]+[dice2]+[stat] = [total]
[Winner label] wins [wager] coins! / It's a draw!
```
6. Log full roll detail and outcome.

**On Decline:** logged only, not announced.

---

### `/scavenge`

Available to signed-up players.

**Cooldown:** 1 hour per player, tracked via `last_scavenge_at` on the player record. On cooldown: ephemeral error showing when the next scavenge is available.

**Behavior:**
1. Checks there is at least one item in the season item pool — if empty, returns ephemeral error.
2. Picks a random stat (`STR`, `INT`, or `ARC`) and a random threshold (`1`–`20`).
3. Rolls 2d6 for the player; total = dice1 + dice2 + player's chosen stat value.
4. **Pass** (`total > threshold`): selects a random item from the season item pool and adds it to the player's inventory.
5. **Fail:** nothing awarded.
6. Updates `last_scavenge_at` on success or fail.
7. Result is sent **ephemerally** to the player and also **logged** to the log channel. Nothing is posted publicly.

Result format (ephemeral):
```text
🔍 SCAVENGE — [stat] Check (Threshold: [threshold])

Dice Roll: [d1] + [d2] = [sum]
[STAT] Stat: +[stat_value]
Result Check: [sum] + [stat_value] = [total] > [threshold]

✅ You found: [item name] — [description]
  or
❌ Nothing found this time.
```

---

### `/leaderboard`

Available to all users (no signup required).

Validation: No active season → `❌ There is no active Server Games season.`

**Behavior:** groups all signed-up players by `map_position`, highest first. Shows the top 5 distinct map position values. Posts the leaderboard to the **announcement channel** (not ephemeral).

Format:
```text
🏆 SERVER GAMES — LEADERBOARD

Map Position 5
• Rogue RedRiot
• Hero BlueSky

Map Position 4
• Mage Wizard99

Map Position 3
• Drakon Crusher
• Bard JazzHands
```

If fewer than 5 distinct positions exist, only the available ones are shown. Players at the same position are listed together under that position heading.

---

## Database (updated)

### `players` — new column

- `last_scavenge_at` — text ISO timestamp, nullable. Tracks when the player last ran `/scavenge`. Used to enforce the 1-hour cooldown.

---

## Startup

On `on_ready`:

1. Initializes or migrates the database.
2. Syncs Discord slash commands.
3. Prints the bot login details to the console.
