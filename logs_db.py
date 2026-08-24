import sqlite3
from datetime import datetime

def init_db():
    con = sqlite3.connect("logs_db.db")
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
            error_message TEXT
        )
    """)
    con.commit()
    con.close()

def log_tool_call(run_id, user_input, tool_name, arguments, tool_output, status, error_message):
    con = sqlite3.connect("logs_db.db")
    cur = con.cursor()
    timestamp = datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO agent_logs (run_id, timestamp, user_input, tool_name, arguments, tool_output, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, timestamp, user_input, tool_name, arguments, tool_output, status, error_message)
        )
    con.commit()
    con.close()

if __name__ == "__main__":
    init_db()