import sqlite3, json, os
from datetime import datetime

DB_PATH = "notebot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            channel TEXT,
            duration_minutes INTEGER,
            speakers TEXT,
            summary TEXT,
            topics TEXT,
            decisions TEXT,
            action_items TEXT,
            blockers TEXT,
            transcript TEXT,
            google_doc_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_meeting(data: dict) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        INSERT INTO meetings
        (date, channel, duration_minutes, speakers, summary, topics,
         decisions, action_items, blockers, transcript, google_doc_url)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
        data.get("channel", ""),
        data.get("duration_minutes", 0),
        json.dumps(data.get("speakers", [])),
        data.get("summary", ""),
        json.dumps(data.get("topics", [])),
        json.dumps(data.get("decisions", [])),
        json.dumps(data.get("action_items", [])),
        json.dumps(data.get("blockers", [])),
        data.get("transcript", ""),
        data.get("google_doc_url", "")
    ))
    meeting_id = cur.lastrowid
    conn.commit()
    conn.close()
    return meeting_id

def get_all_meetings() -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM meetings ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_meeting(meeting_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM meetings WHERE id=?", (meeting_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
