import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
JSON_PATH = BASE_DIR / "data.json"
DB_PATH = BASE_DIR / "bot.sqlite3"
OWNER_ID = 827744412


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def normalize_username(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS participants (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                tg_id INTEGER,
                invite_link TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_username TEXT NOT NULL,
                invited_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                removed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (participant_username)
                    REFERENCES participants(username)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contest_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER NOT NULL DEFAULT 0,
                chat_id INTEGER,
                started_at TEXT
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))
        conn.execute(
            "INSERT OR IGNORE INTO contest_state (id, active, chat_id, started_at) VALUES (1, 0, NULL, NULL)"
        )


def main():
    if not JSON_PATH.exists():
        print("data.json не найден")
        return

    init_db()

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    admins = data.get("admins", [])
    participants = data.get("participants", {})
    contest = data.get("contest", {}) if isinstance(data.get("contest"), dict) else {}

    imported_participants = 0
    imported_invites = 0

    with get_conn() as conn:
        conn.execute("DELETE FROM invites")
        conn.execute("DELETE FROM participants")
        conn.execute("DELETE FROM admins")
        conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))

        for admin_id in admins:
            try:
                conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(admin_id),))
            except Exception:
                pass

        conn.execute(
            "UPDATE contest_state SET active = ?, chat_id = ?, started_at = ? WHERE id = 1",
            (
                1 if contest.get("active") else 0,
                contest.get("chat_id"),
                contest.get("started_at"),
            ),
        )

        if isinstance(participants, dict):
            for raw_uname, p in participants.items():
                uname = normalize_username(raw_uname)
                if not uname or not isinstance(p, dict):
                    continue

                conn.execute(
                    """
                    INSERT INTO participants (username, display_name, tg_id, invite_link, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        uname,
                        p.get("name") or uname,
                        p.get("tg_id"),
                        p.get("invite_link") or "",
                        now_str(),
                    ),
                )
                imported_participants += 1

                invites = p.get("invites", [])
                if isinstance(invites, list):
                    for inv in invites:
                        if not isinstance(inv, dict):
                            continue
                        conn.execute(
                            """
                            INSERT INTO invites (participant_username, invited_name, added_at, removed)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                uname,
                                inv.get("name") or "Без имени",
                                inv.get("added_at") or now_str(),
                                1 if inv.get("removed") else 0,
                            ),
                        )
                        imported_invites += 1

    print("Готово.")
    print(f"Участников импортировано: {imported_participants}")
    print(f"Инвайтов импортировано: {imported_invites}")
    print(f"База: {DB_PATH}")


if __name__ == "__main__":
    main()
