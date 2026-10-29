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

                -- Motor de recomendación: estado por pasos, decisiones auditables, leads y etiquetas.
                CREATE TABLE IF NOT EXISTS motor_state (
                    conversation_id INTEGER PRIMARY KEY,
                    engine_mode TEXT,
                    state_json TEXT DEFAULT '{}',
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS motor_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    engine_mode TEXT,
                    task TEXT,
                    provider TEXT,
                    input TEXT,
                    output TEXT,
                    confidence REAL,
                    latency_ms INTEGER,
                    ok INTEGER,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS motor_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    course_slug TEXT,
                    kind TEXT,
                    created_at TEXT,
                    UNIQUE(conversation_id, course_slug, kind)
                );
                CREATE TABLE IF NOT EXISTS motor_tags (
                    slug TEXT PRIMARY KEY,
                    hash TEXT,
                    tags_json TEXT,
                    source TEXT,
                    updated_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_motor_decisions_conv ON motor_decisions(conversation_id, id);
                -- Eventos de negocio y de calidad del chat, base del panel de analítica.
                CREATE TABLE IF NOT EXISTS motor_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    engine_mode TEXT,
                    kind TEXT NOT NULL,
                    course_slug TEXT,
                    value TEXT,
                    amount REAL,
                    created_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_motor_events_kind ON motor_events(kind, created_at);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(courses)")}
            if "signup_url" not in columns:
                conn.execute("ALTER TABLE courses ADD COLUMN signup_url TEXT DEFAULT ''")
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
                    prices_json, contents, note, updated_at, signup_url
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    course.get("signup_url") or "",
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


# ---------------------------------------------------------------- motor de recomendación

def motor_get_state(conversation_id: int) -> tuple[str | None, dict]:
    def run(conn):
        row = conn.execute("SELECT * FROM motor_state WHERE conversation_id=?", (conversation_id,)).fetchone()
        return (row["engine_mode"], json.loads(row["state_json"] or "{}")) if row else (None, {})

    return _tx(run)


def motor_save_state(conversation_id: int, engine_mode: str, state: dict) -> None:
    def run(conn):
        conn.execute(
            """INSERT INTO motor_state(conversation_id, engine_mode, state_json, updated_at) VALUES(?,?,?,?)
               ON CONFLICT(conversation_id) DO UPDATE SET engine_mode=excluded.engine_mode,
               state_json=excluded.state_json, updated_at=excluded.updated_at""",
            (conversation_id, engine_mode, json.dumps(state, ensure_ascii=False), now()),
        )

    _tx(run)


def motor_set_engine_mode(conversation_id: int, engine_mode: str) -> None:
    _, state = motor_get_state(conversation_id)
    motor_save_state(conversation_id, engine_mode, state)


def motor_log_decision(conversation_id, engine_mode, task, provider, input_, output, confidence, latency_ms, ok) -> None:
    def run(conn):
        conn.execute(
            """INSERT INTO motor_decisions(conversation_id, engine_mode, task, provider, input, output,
               confidence, latency_ms, ok, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (conversation_id, engine_mode, task, provider,
             json.dumps(input_, ensure_ascii=False, default=str)[:4000],
             json.dumps(output, ensure_ascii=False, default=str)[:4000],
             confidence, latency_ms, int(bool(ok)), now()),
        )

    _tx(run)


def motor_decisions(conversation_id: int, limit: int = 40) -> list[dict]:
    def run(conn):
        rows = conn.execute(
            "SELECT * FROM motor_decisions WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    return _tx(run)


def motor_add_lead(conversation_id: int, course_slug: str | None, kind: str) -> None:
    def run(conn):
        conn.execute(
            "INSERT OR IGNORE INTO motor_leads(conversation_id, course_slug, kind, created_at) VALUES(?,?,?,?)",
            (conversation_id, course_slug, kind, now()),
        )

    _tx(run)


def motor_leads() -> list[dict]:
    def run(conn):
        rows = conn.execute(
            """SELECT l.*, c.user_name, c.username, c.origin, s.engine_mode FROM motor_leads l
               JOIN conversations c ON c.id=l.conversation_id
               LEFT JOIN motor_state s ON s.conversation_id=l.conversation_id ORDER BY l.id DESC"""
        ).fetchall()
        return [dict(row) for row in rows]

    return _tx(run)


def motor_tags() -> dict[str, dict]:
    def run(conn):
        rows = conn.execute("SELECT * FROM motor_tags").fetchall()
        return {row["slug"]: {"hash": row["hash"], "tags": json.loads(row["tags_json"] or "null"),
                              "source": row["source"]} for row in rows}

    return _tx(run)


def motor_set_tags(slug: str, digest: str | None, tags: dict | None, source: str | None) -> None:
    def run(conn):
        conn.execute(
            """INSERT INTO motor_tags(slug, hash, tags_json, source, updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET hash=COALESCE(excluded.hash, motor_tags.hash),
               tags_json=excluded.tags_json, source=excluded.source, updated_at=excluded.updated_at""",
            (slug, digest, json.dumps(tags, ensure_ascii=False) if tags is not None else None, source, now()),
        )

    _tx(run)


def motor_metrics() -> list[dict]:
    """Comparación de modos del motor a partir de las conversaciones y decisiones registradas."""
    def run(conn):
        out = []
        modes = [r["engine_mode"] for r in conn.execute(
            "SELECT DISTINCT engine_mode FROM motor_state WHERE engine_mode IS NOT NULL")]
        for mode in modes:
            ids = [r["conversation_id"] for r in conn.execute(
                "SELECT conversation_id FROM motor_state WHERE engine_mode=?", (mode,))]
            ph = ",".join("?" * len(ids)) or "NULL"
            handoffs = conn.execute(
                f"SELECT COUNT(*) n FROM conversations WHERE id IN ({ph}) AND handover_reason<>''", ids).fetchone()["n"]
            recommended = conn.execute(
                f"SELECT COUNT(DISTINCT conversation_id) n FROM motor_leads WHERE kind='recomendado' AND conversation_id IN ({ph})",
                ids).fetchone()["n"]
            notify = conn.execute(
                f"SELECT COUNT(*) n FROM motor_leads WHERE kind LIKE 'avisar%' AND conversation_id IN ({ph})", ids).fetchone()["n"]
            providers = [dict(r) for r in conn.execute(
                """SELECT provider, COUNT(*) n, AVG(latency_ms) latency_ms, AVG(confidence) confidence,
                   SUM(ok=0) errors FROM motor_decisions WHERE engine_mode=? GROUP BY provider""", (mode,))]
            out.append({"engine_mode": mode, "conversations": len(ids), "handoffs": handoffs,
                        "recommended": recommended, "notify_requests": notify, "providers": providers})
        return out

    return _tx(run)


def motor_event(conversation_id, engine_mode, kind, course_slug=None, value=None, amount=None) -> None:
    def run(conn):
        conn.execute(
            """INSERT INTO motor_events(conversation_id, engine_mode, kind, course_slug, value, amount, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (conversation_id, engine_mode, kind, course_slug, value, amount, now()),
        )

    _tx(run)


def motor_analytics_raw(since: str) -> dict:
    """Datos crudos para la analítica desde `since` (ISO). La composición vive en app/motor/analytics.py."""
    def run(conn):
        q = lambda sql, *args: [dict(r) for r in conn.execute(sql, args).fetchall()]
        return {
            "conversations": q(
                """SELECT c.id, c.mode, c.mood, c.handover_reason, c.origin, c.created_at, c.user_name, c.username,
                          COALESCE(s.engine_mode, '') engine_mode, COALESCE(s.state_json, '{}') state_json
                   FROM conversations c LEFT JOIN motor_state s ON s.conversation_id=c.id
                   WHERE c.created_at >= ?""", since),
            "events": q("SELECT * FROM motor_events WHERE created_at >= ? ORDER BY id", since),
            "decisions": q(
                """SELECT engine_mode, provider, task, latency_ms, confidence, ok FROM motor_decisions
                   WHERE created_at >= ? AND conversation_id IS NOT NULL""", since),
            "messages": q(
                """SELECT m.conversation_id, m.role, m.created_at FROM messages m
                   JOIN conversations c ON c.id=m.conversation_id WHERE c.created_at >= ? ORDER BY m.id""", since),
            "courses": q("SELECT slug, title, computed_status, prices_json FROM courses"),
        }

    return _tx(run)
