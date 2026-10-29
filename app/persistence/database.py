import json
import sqlite3
import threading
from datetime import datetime, timezone

from app.config import DB_PATH

_lock = threading.Lock()

STATUS_LABEL = {
    "inscripcion_abierta": "Inscripción abierta",
    "por_abrir": "Inscripción por abrir",
    "inscripcion_cerrada": "Inscripción cerrada",
    "en_curso": "En curso",
    "finalizado": "Finalizado",
    "sin_fecha": "Sin fechas publicadas",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock:
        conn = connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    slug TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    hours INTEGER,
                    listing_status TEXT,
                    computed_status TEXT,
                    description TEXT,
                    objectives TEXT,
                    place TEXT,
                    instructors TEXT,
                    enrollment_start TEXT,
                    enrollment_end TEXT,
                    course_start TEXT,
                    course_end TEXT,
                    schedule TEXT,
                    modality TEXT,
                    capacity TEXT,
                    prerequisites TEXT,
                    certificate TEXT,
                    prices_json TEXT,
                    contents TEXT,
                    note TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_chat_id INTEGER UNIQUE,
                    user_name TEXT,
                    username TEXT,
                    origin TEXT DEFAULT 'telegram',
                    mode TEXT DEFAULT 'ai',
                    mood TEXT DEFAULT 'interesado',
                    intent TEXT DEFAULT 'otro',
                    summary TEXT DEFAULT '',
                    profile_json TEXT DEFAULT '{}',
                    handover_reason TEXT DEFAULT '',
                    flagged INTEGER DEFAULT 0,
                    courses_json TEXT DEFAULT '[]',
                    last_message TEXT DEFAULT '',
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
                CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
                """
            )
            conn.commit()
        finally:
            conn.close()


def _tx(fn):
    with _lock:
        conn = connect()
        try:
            result = fn(conn)
            conn.commit()
            return result
        finally:
            conn.close()


def set_meta(key: str, value: str) -> None:
    def run(conn):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    _tx(run)


def get_meta(key: str, default: str = "") -> str:
    def run(conn):
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    return _tx(run)


def replace_courses(courses: list[dict]) -> int:
    def run(conn):
        conn.execute("DELETE FROM courses")
        for course in courses:
            conn.execute(
                """
                INSERT INTO courses (
                    slug, url, title, hours, listing_status, computed_status,
                    description, objectives, place, instructors,
                    enrollment_start, enrollment_end, course_start, course_end,
                    schedule, modality, capacity, prerequisites, certificate,
                    prices_json, contents, note, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    course["slug"],
                    course["url"],
                    course["title"],
                    course.get("hours"),
                    course.get("listing_status") or "",
                    course.get("computed_status") or "sin_fecha",
                    course.get("description") or "",
                    course.get("objectives") or "",
                    course.get("place") or "",
                    course.get("instructors") or "",
                    course.get("enrollment_start"),
                    course.get("enrollment_end"),
                    course.get("course_start"),
                    course.get("course_end"),
                    course.get("schedule") or "",
                    course.get("modality") or "",
                    course.get("capacity") or "",
                    course.get("prerequisites") or "",
                    course.get("certificate") or "",
                    json.dumps(course.get("prices") or {}, ensure_ascii=False),
                    course.get("contents") or "",
                    course.get("note") or "",
                    now(),
                ),
            )
        return len(courses)

    return _tx(run)


def _course_out(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["prices"] = json.loads(data.pop("prices_json") or "{}")
    data["status_label"] = STATUS_LABEL.get(data.get("computed_status"), data.get("computed_status"))
    return data


def list_courses() -> list[dict]:
    def run(conn):
        rows = conn.execute("SELECT * FROM courses ORDER BY title COLLATE NOCASE").fetchall()
        return [_course_out(row) for row in rows]

    return _tx(run)


def get_course(slug: str) -> dict | None:
    def run(conn):
        row = conn.execute("SELECT * FROM courses WHERE slug=?", (slug,)).fetchone()
        return _course_out(row) if row else None

    return _tx(run)


def _conversation_out(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["profile"] = json.loads(data.pop("profile_json") or "{}")
    data["courses"] = json.loads(data.pop("courses_json") or "[]")
    data["flagged"] = bool(data["flagged"])
    return data


def get_or_create_conversation(
    telegram_chat_id: int,
    user_name: str,
    username: str,
    origin: str = "telegram",
) -> dict:
    def run(conn):
        row = conn.execute(
            "SELECT * FROM conversations WHERE telegram_chat_id=?",
            (telegram_chat_id,),
        ).fetchone()
        if row:
            if user_name and user_name != row["user_name"]:
                conn.execute(
                    "UPDATE conversations SET user_name=?, username=? WHERE id=?",
                    (user_name, username or row["username"], row["id"]),
                )
                row = conn.execute("SELECT * FROM conversations WHERE id=?", (row["id"],)).fetchone()
            return _conversation_out(row)
        stamp = now()
        cur = conn.execute(
            """
            INSERT INTO conversations (
                telegram_chat_id, user_name, username, origin, created_at, updated_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (telegram_chat_id, user_name or "Prospecto", username or "", origin, stamp, stamp),
        )
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (cur.lastrowid,)).fetchone()
        return _conversation_out(row)

    return _tx(run)


def get_conversation(conversation_id: int) -> dict | None:
    def run(conn):
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return _conversation_out(row) if row else None

    return _tx(run)


def list_conversations() -> list[dict]:
    def run(conn):
        rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        return [_conversation_out(row) for row in rows]

    return _tx(run)


def add_message(conversation_id: int, role: str, content: str) -> dict:
    def run(conn):
        stamp = now()
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
            (conversation_id, role, content, stamp),
        )
        preview = content.replace("\n", " ")[:140]
        conn.execute(
            "UPDATE conversations SET last_message=?, updated_at=? WHERE id=?",
            (preview, stamp, conversation_id),
        )
        return {
            "id": cur.lastrowid,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": stamp,
        }

    return _tx(run)


def list_messages(conversation_id: int) -> list[dict]:
    def run(conn):
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    return _tx(run)


def recent_messages(conversation_id: int, limit: int = 8) -> list[dict]:
    def run(conn):
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (conversation_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    return _tx(run)


def update_conversation(conversation_id: int, **fields) -> dict | None:
    allowed = {
        "mode",
        "mood",
        "intent",
        "summary",
        "profile_json",
        "handover_reason",
        "flagged",
        "courses_json",
        "user_name",
    }
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        values.append(value)
    if not sets:
        return get_conversation(conversation_id)
    sets.append("updated_at=?")
    values.append(now())
    values.append(conversation_id)

    def run(conn):
        conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id=?", values)
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return _conversation_out(row) if row else None

    return _tx(run)


def metrics() -> dict:
    def run(conn):
        total = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
        human = conn.execute("SELECT COUNT(*) AS n FROM conversations WHERE mode='human'").fetchone()["n"]
        flagged = conn.execute("SELECT COUNT(*) AS n FROM conversations WHERE flagged=1").fetchone()["n"]
        moods = {"interesado": 0, "dudoso": 0, "frustrado": 0, "caliente": 0}
        for row in conn.execute("SELECT mood, COUNT(*) AS n FROM conversations GROUP BY mood"):
            if row["mood"] in moods:
                moods[row["mood"]] = row["n"]
        messages = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        courses = conn.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"]
        return {
            "conversations": total,
            "with_human": human,
            "with_ai": total - human,
            "flagged": flagged,
            "moods": moods,
            "messages": messages,
            "courses": courses,
            "catalog_updated_at": get_meta_unlocked(conn, "catalog_updated_at"),
            "catalog_error": get_meta_unlocked(conn, "catalog_error"),
            "catalog_refreshing": get_meta_unlocked(conn, "catalog_refreshing") == "1",
        }

    return _tx(run)


def get_meta_unlocked(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""
