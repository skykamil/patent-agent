import sqlite3
from datetime import datetime

def get_connection():
    con = sqlite3.connect("logs_db.db")
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
    con.commit()
    con.close()

def log_tool_call(run_id, user_input, tool_name, arguments, tool_output, status, error_message, final_response):
    con = get_connection()
    cur = con.cursor()
    timestamp = datetime.now().isoformat()
    cur.execute(
        """
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