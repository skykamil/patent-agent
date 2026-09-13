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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS request_logs(
        id INTEGER PRIMARY KEY,
        run_id TEXT,
        conversation_id TEXT,
        timestamp TEXT,
        status_code INTEGER,
        latency_ms REAL,
        error_type TEXT,
        error_message TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        total_tokens INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS retry_logs(
        id INTEGER PRIMARY KEY,
        run_id TEXT,
        tool_call_id TEXT,
        tool_name TEXT,
        timestamp TEXT,
        service TEXT,
        attempt INTEGER,
        reason TEXT,
        wait_seconds REAL
        )
    """)
    con.commit()
    con.close()

def log_retry(run_id, tool_call_id, tool_name, service, attempt, reason, wait_seconds):
    con = None
    try:
        con = get_connection()
        cur = con.cursor()
        timestamp = datetime.now().isoformat()
        cur.execute(
            """
            INSERT INTO retry_logs(run_id, tool_call_id, tool_name, timestamp, service, attempt, reason, wait_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, tool_call_id, tool_name, timestamp, service, attempt, reason, wait_seconds)
        )
        con.commit()
    except sqlite3.Error:
        return
    finally:
        if con is not None:
            con.close()

def log_request(run_id, conversation_id, status_code, latency_ms, error_type, error_message, input_tokens, output_tokens):
    con = get_connection()
    cur = con.cursor()
    timestamp = datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO request_logs(run_id, conversation_id, timestamp, status_code, latency_ms, error_type, error_message, input_tokens, output_tokens, total_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, conversation_id, timestamp, status_code, latency_ms, error_type, error_message, input_tokens, output_tokens, input_tokens+output_tokens)
    )
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