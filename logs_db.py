import os
import sqlite3
from datetime import datetime

DATABASE_PATH = os.getenv("DATABASE_PATH", "logs_db.db")

def get_connection():
    con = sqlite3.connect(DATABASE_PATH)
    return con

def init_db():
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_logs(
            id INTEGER PRIMARY KEY,
            run_id TEXT, 
            timestamp TEXT, 
            user_input TEXT, 
            tool_name TEXT, 
            arguments TEXT, 
            tool_output TEXT, 
            status TEXT,
            error_message TEXT,
            final_response TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations(
            conversation_id TEXT PRIMARY KEY,
            history TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_usage(
            day TEXT PRIMARY KEY,
            request_count INTEGER NOT NULL
        )
    """)
    con.commit()
    con.close()

def try_increment_daily_usage(day: str, limit: int) -> bool:
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO daily_usage (day, request_count) VALUES (?, 1)
        ON CONFLICT (day) DO UPDATE SET
            request_count=request_count+1
            WHERE request_count < ?
        """,
        (day, limit)
    )
    incremented = cur.rowcount == 1
    con.commit()
    con.close()
    return incremented

def save_conversation(conversation_id, history):
    con = get_connection()
    cur = con.cursor()
    timestamp = datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO conversations (conversation_id, history, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            history=excluded.history,
            updated_at=excluded.updated_at
        """,
        (conversation_id, history, timestamp, timestamp)
    )
    con.commit()
    con.close()

def load_conversation(conversation_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT history FROM conversations WHERE conversation_id = ?", (conversation_id,))
    row = cur.fetchone()
    con.close()
    if row is None:
        return None
    return row[0]

def log_tool_call(run_id, user_input, tool_name, arguments, tool_output, status, error_message, final_response):
    con = get_connection()
    cur = con.cursor()
    timestamp = datetime.now().isoformat()
    cur.execute("""
        INSERT INTO agent_logs (run_id, timestamp, user_input, tool_name, arguments, tool_output, status, error_message, final_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, timestamp, user_input, tool_name, arguments, tool_output, status, error_message, final_response)
    )
    log_id = cur.lastrowid
    con.commit()
    con.close()
    return log_id

def update_final_response(log_id, final_response):
    con = get_connection()
    cur = con.cursor()
    cur.execute("UPDATE agent_logs SET final_response = ? WHERE id = ?", (final_response, log_id))
    con.commit()
    con.close()

if __name__ == "__main__":
    init_db()