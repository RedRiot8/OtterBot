import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "servergames.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_number INTEGER UNIQUE NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                announcement_channel_id TEXT,
                log_channel_id TEXT,
                gamemaster_role_id TEXT,
                player_role_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                discord_username TEXT NOT NULL,
                season_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                str_stat INTEGER NOT NULL,
                int_stat INTEGER NOT NULL,
                arc_stat INTEGER NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0,
                map_position INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(discord_id, season_id),
                FOREIGN KEY (season_id) REFERENCES seasons(id)
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                scavengable INTEGER NOT NULL DEFAULT 0,
                rarity TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (season_id) REFERENCES seasons(id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                season_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                stat TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                reward_type TEXT NOT NULL,
                reward_amount INTEGER,
                reward_item_id INTEGER,
                reward_2_type TEXT,
                reward_2_amount INTEGER,
                fail_message TEXT,
                is_secret INTEGER NOT NULL DEFAULT 0,
                secret_targets TEXT,
                secret_target_type TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(event_id, season_id),
                FOREIGN KEY (season_id) REFERENCES seasons(id),
                FOREIGN KEY (reward_item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS rolls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                dice1 INTEGER NOT NULL,
                dice2 INTEGER NOT NULL,
                stat_modifier INTEGER NOT NULL,
                total INTEGER NOT NULL,
                threshold INTEGER NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(player_id, event_id),
                FOREIGN KEY (player_id) REFERENCES players(id),
                FOREIGN KEY (event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS player_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                acquired_at TEXT NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id),
                FOREIGN KEY (item_id) REFERENCES items(id)
            );
            """
        )

        # Migrate any EVT-XXXX codes back to plain 3-digit format
        conn.execute(
            """
            UPDATE events
            SET event_id = printf('%03d', CAST(SUBSTR(event_id, 5) AS INTEGER))
            WHERE event_id LIKE 'EVT-%'
            """
        )

        # Column migrations for existing databases
        item_cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        for col, definition in [
            ("scavengable", "INTEGER NOT NULL DEFAULT 0"),
            ("rarity", "TEXT"),
        ]:
            if col not in item_cols:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} {definition}")

        player_cols = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
        if "last_scavenge_at" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN last_scavenge_at TEXT")

        season_cols = {row[1] for row in conn.execute("PRAGMA table_info(seasons)")}
        if "gamemaster_role_id" not in season_cols:
            conn.execute("ALTER TABLE seasons ADD COLUMN gamemaster_role_id TEXT")
        if "player_role_id" not in season_cols:
            conn.execute("ALTER TABLE seasons ADD COLUMN player_role_id TEXT")

        event_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        for col, definition in [
            ("reward_2_type", "TEXT"),
            ("reward_2_amount", "INTEGER"),
            ("fail_message", "TEXT"),
            ("reward_item_id", "INTEGER"),
            ("is_secret", "INTEGER NOT NULL DEFAULT 0"),
            ("secret_targets", "TEXT"),
            ("secret_target_type", "TEXT"),
            ("map_position_required", "INTEGER"),
            ("success_message", "TEXT"),
        ]:
            if col not in event_cols:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {definition}")

        # reward_amount may need to allow NULL for ITEM rewards
        # SQLite doesn't support ALTER COLUMN, so we handle this in app logic


def wipe_all() -> None:
    """Delete every row from every table. Used by /servergames test to reset to a clean slate."""
    with get_connection() as conn:
        conn.execute("DELETE FROM rolls")
        conn.execute("DELETE FROM player_inventory")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM items")
        conn.execute("DELETE FROM players")
        conn.execute("DELETE FROM seasons")
        try:
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name IN "
                "('seasons','players','items','events','rolls','player_inventory')"
            )
        except sqlite3.OperationalError:
            pass


def _now() -> str:
    return datetime.utcnow().isoformat()


# ── Seasons ──────────────────────────────────────────────────────────────────

def get_active_season() -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM seasons WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()


def get_season_by_number(season_number: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM seasons WHERE season_number = ?", (season_number,)
        ).fetchone()


def get_next_season_number() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(season_number) AS max_num FROM seasons"
        ).fetchone()
        return (row["max_num"] or 0) + 1


def create_season(season_number: int) -> sqlite3.Row:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO seasons (season_number, is_active, created_at) VALUES (?, 1, ?)",
            (season_number, _now()),
        )
        season_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute(
            "SELECT * FROM seasons WHERE id = ?", (season_id,)
        ).fetchone()


def end_season(season_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE seasons SET is_active = 0 WHERE id = ?", (season_id,))


def configure_season(
    season_id: int,
    announcement_channel_id: str,
    log_channel_id: str,
    gamemaster_role_id: str | None = None,
    player_role_id: str | None = None,
) -> None:
    updates = ["announcement_channel_id = ?", "log_channel_id = ?"]
    params: list = [announcement_channel_id, log_channel_id]
    if gamemaster_role_id is not None:
        updates.append("gamemaster_role_id = ?")
        params.append(gamemaster_role_id)
    if player_role_id is not None:
        updates.append("player_role_id = ?")
        params.append(player_role_id)
    params.append(season_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE seasons SET {', '.join(updates)} WHERE id = ?", params
        )


# ── Players ───────────────────────────────────────────────────────────────────

def count_players(season_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM players WHERE season_id = ?", (season_id,)
        ).fetchone()
        return row["count"]


def get_player(discord_id: str, season_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM players WHERE discord_id = ? AND season_id = ?",
            (discord_id, season_id),
        ).fetchone()


def create_player(
    discord_id: str,
    discord_username: str,
    season_id: int,
    class_name: str,
    str_stat: int,
    int_stat: int,
    arc_stat: int,
) -> sqlite3.Row:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO players (
                discord_id, discord_username, season_id, class_name,
                str_stat, int_stat, arc_stat, coins, map_position, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
            """,
            (discord_id, discord_username, season_id, class_name,
             str_stat, int_stat, arc_stat, _now()),
        )
        player_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute(
            "SELECT * FROM players WHERE id = ?", (player_id,)
        ).fetchone()


def get_leaderboard(season_id: int, top_positions: int = 5) -> list[sqlite3.Row]:
    """Return all players ordered by map_position desc, limited to those within the top N distinct positions."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT map_position FROM players WHERE season_id = ? ORDER BY map_position DESC LIMIT ?",
            (season_id, top_positions),
        ).fetchall()
        if not rows:
            return []
        cutoff = rows[-1]["map_position"]
        return conn.execute(
            "SELECT * FROM players WHERE season_id = ? AND map_position >= ? ORDER BY map_position DESC, discord_username ASC",
            (season_id, cutoff),
        ).fetchall()


def update_player_field(player_id: int, field: str, new_value: int) -> None:
    allowed = {"str_stat", "int_stat", "arc_stat", "coins", "map_position"}
    if field not in allowed:
        raise ValueError(f"Invalid field: {field}")
    with get_connection() as conn:
        conn.execute(
            f"UPDATE players SET {field} = ? WHERE id = ?", (new_value, player_id)
        )


# ── Items ─────────────────────────────────────────────────────────────────────

def create_item(
    season_id: int,
    name: str,
    description: str,
    scavengable: bool = False,
    rarity: str | None = None,
) -> sqlite3.Row:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO items (season_id, name, description, scavengable, rarity, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (season_id, name, description, 1 if scavengable else 0, rarity, _now()),
        )
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def update_item_fields(item_id: int, updates: dict) -> sqlite3.Row | None:
    allowed = {"name", "description", "scavengable", "rarity"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return None
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    params = list(filtered.values()) + [item_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE items SET {set_clause} WHERE id = ?", params)
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def get_scavengable_items(season_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM items WHERE season_id = ? AND scavengable = 1 ORDER BY name",
            (season_id,),
        ).fetchall()


def update_player_scavenge_time(player_id: int, timestamp: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE players SET last_scavenge_at = ? WHERE id = ?", (timestamp, player_id)
        )


def get_items(season_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM items WHERE season_id = ? ORDER BY name", (season_id,)
        ).fetchall()


def get_item_by_id(item_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()


def get_players_with_item(item_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT p.* FROM players p
            JOIN player_inventory pi ON pi.player_id = p.id
            WHERE pi.item_id = ?
            """,
            (item_id,),
        ).fetchall()


def get_events_with_item(item_id: int, season_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE reward_item_id = ? AND season_id = ? AND is_active = 1",
            (item_id, season_id),
        ).fetchall()


def remove_item_cascade(item_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM player_inventory WHERE item_id = ?", (item_id,))
        conn.execute(
            "UPDATE events SET reward_item_id = NULL WHERE reward_item_id = ?",
            (item_id,),
        )
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


# ── Player Inventory ──────────────────────────────────────────────────────────

def add_player_inventory(player_id: int, item_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO player_inventory (player_id, item_id, acquired_at) VALUES (?, ?, ?)",
            (player_id, item_id, _now()),
        )


def remove_player_inventory(player_id: int, item_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM player_inventory WHERE id = (
                SELECT id FROM player_inventory
                WHERE player_id = ? AND item_id = ?
                LIMIT 1
            )
            """,
            (player_id, item_id),
        )


def get_player_inventory(player_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT i.* FROM items i
            JOIN player_inventory pi ON pi.item_id = i.id
            WHERE pi.player_id = ?
            ORDER BY pi.acquired_at
            """,
            (player_id,),
        ).fetchall()


# ── Events ────────────────────────────────────────────────────────────────────

def normalize_event_code(event_code: str) -> str:
    code = event_code.strip().upper().replace("EVT-", "")
    if code.isdigit():
        return f"{int(code):03d}"
    return code


def next_event_code(season_id: int) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM events WHERE season_id = ?", (season_id,)
        ).fetchone()
        return f"{row['count'] + 1:03d}"


def create_event(
    season_id: int,
    event_code: str,
    name: str,
    description: str,
    stat: str,
    threshold: int,
    reward_type: str,
    reward_amount: int | None,
    reward_item_id: int | None = None,
    reward_2_type: str | None = None,
    reward_2_amount: int | None = None,
    fail_message: str | None = None,
    success_message: str | None = None,
    is_secret: bool = False,
    secret_targets: list[str] | None = None,
    secret_target_type: str | None = None,
    map_position_required: int | None = None,
) -> sqlite3.Row:
    secret_targets_json = json.dumps(secret_targets) if secret_targets else None
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (
                event_id, season_id, name, description, stat, threshold,
                reward_type, reward_amount, reward_item_id,
                reward_2_type, reward_2_amount, fail_message, success_message,
                is_secret, secret_targets, secret_target_type,
                map_position_required, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                event_code, season_id, name, description, stat, threshold,
                reward_type, reward_amount, reward_item_id,
                reward_2_type, reward_2_amount, fail_message, success_message,
                1 if is_secret else 0,
                secret_targets_json, secret_target_type,
                map_position_required, _now(),
            ),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def get_event_by_code(
    event_code: str, season_id: int, *, active_only: bool = True
) -> sqlite3.Row | None:
    normalized = normalize_event_code(event_code)
    query = "SELECT * FROM events WHERE event_id = ? AND season_id = ?"
    params: list = [normalized, season_id]
    if active_only:
        query += " AND is_active = 1"
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()


def get_active_events(season_id: int, *, include_secret: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM events WHERE season_id = ? AND is_active = 1"
    if not include_secret:
        query += " AND is_secret = 0"
    query += " ORDER BY event_id"
    with get_connection() as conn:
        return conn.execute(query, (season_id,)).fetchall()


def update_event_fields(event_db_id: int, updates: dict) -> sqlite3.Row | None:
    allowed = {
        "name", "description", "stat", "threshold",
        "reward_type", "reward_amount", "reward_item_id",
        "reward_2_type", "reward_2_amount", "fail_message", "success_message",
        "is_secret", "secret_targets", "secret_target_type",
        "map_position_required",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return None
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    params = list(filtered.values()) + [event_db_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE events SET {set_clause} WHERE id = ?", params
        )
        return conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_db_id,)
        ).fetchone()


def deactivate_event(event_code: str, season_id: int) -> sqlite3.Row | None:
    event = get_event_by_code(event_code, season_id, active_only=True)
    if not event:
        return None
    with get_connection() as conn:
        conn.execute("UPDATE events SET is_active = 0 WHERE id = ?", (event["id"],))
    return event


# ── Rolls ─────────────────────────────────────────────────────────────────────

def has_player_rolled(player_id: int, event_db_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM rolls WHERE player_id = ? AND event_id = ?",
            (player_id, event_db_id),
        ).fetchone()
        return row is not None


def create_roll(
    player_id: int,
    event_db_id: int,
    dice1: int,
    dice2: int,
    stat_modifier: int,
    total: int,
    threshold: int,
    result: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO rolls (
                player_id, event_id, dice1, dice2, stat_modifier,
                total, threshold, result, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (player_id, event_db_id, dice1, dice2, stat_modifier,
             total, threshold, result, _now()),
        )
