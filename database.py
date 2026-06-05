import sqlite3
from datetime import datetime
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "servergames.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
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

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                season_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                stat TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                reward_type TEXT NOT NULL,
                reward_amount INTEGER NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(event_id, season_id),
                FOREIGN KEY (season_id) REFERENCES seasons(id)
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
            """
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(seasons)")
        }
        if "gamemaster_role_id" not in columns:
            conn.execute(
                "ALTER TABLE seasons ADD COLUMN gamemaster_role_id TEXT"
            )
        if "player_role_id" not in columns:
            conn.execute("ALTER TABLE seasons ADD COLUMN player_role_id TEXT")


def _now() -> str:
    return datetime.utcnow().isoformat()


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
            """
            INSERT INTO seasons (season_number, is_active, created_at)
            VALUES (?, 1, ?)
            """,
            (season_number, _now()),
        )
        season_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute(
            "SELECT * FROM seasons WHERE id = ?", (season_id,)
        ).fetchone()


def end_season(season_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE seasons SET is_active = 0 WHERE id = ?", (season_id,)
        )


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
            f"UPDATE seasons SET {', '.join(updates)} WHERE id = ?",
            params,
        )


def count_players(season_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM players WHERE season_id = ?",
            (season_id,),
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
            (
                discord_id,
                discord_username,
                season_id,
                class_name,
                str_stat,
                int_stat,
                arc_stat,
                _now(),
            ),
        )
        player_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute(
            "SELECT * FROM players WHERE id = ?", (player_id,)
        ).fetchone()


def update_player_field(player_id: int, field: str, new_value: int) -> None:
    allowed = {
        "str_stat",
        "int_stat",
        "arc_stat",
        "coins",
        "map_position",
    }
    if field not in allowed:
        raise ValueError(f"Invalid field: {field}")
    with get_connection() as conn:
        conn.execute(
            f"UPDATE players SET {field} = ? WHERE id = ?", (new_value, player_id)
        )


def normalize_event_code(event_code: str) -> str:
    code = event_code.strip().upper().replace("EVT-", "")
    if code.isdigit():
        return f"{int(code):03d}"
    return code


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


def get_active_events(season_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM events
            WHERE season_id = ? AND is_active = 1
            ORDER BY CAST(event_id AS INTEGER)
            """,
            (season_id,),
        ).fetchall()


def deactivate_event(event_code: str, season_id: int) -> sqlite3.Row | None:
    event = get_event_by_code(event_code, season_id, active_only=True)
    if not event:
        return None
    with get_connection() as conn:
        conn.execute(
            "UPDATE events SET is_active = 0 WHERE id = ?", (event["id"],)
        )
    return event


def next_event_code(season_id: int) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM events WHERE season_id = ?",
            (season_id,),
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
    reward_amount: int,
) -> sqlite3.Row:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (
                event_id, season_id, name, description, stat, threshold,
                reward_type, reward_amount, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                event_code,
                season_id,
                name,
                description,
                stat,
                threshold,
                reward_type,
                reward_amount,
                _now(),
            ),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()


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
            (
                player_id,
                event_db_id,
                dice1,
                dice2,
                stat_modifier,
                total,
                threshold,
                result,
                _now(),
            ),
        )
