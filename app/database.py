import sqlite3
from contextlib import contextmanager
from .config import DB_PATH, MAX_TABS


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                plot_path TEXT,
                table_path TEXT,
                chart_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(id)
            )
        """)
        conn.commit()
        # migracao p/ bancos criados antes das colunas novas existirem
        for coluna in ("table_path", "chart_data"):
            try:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {coluna} TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass


def create_chat(name: str) -> int:
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) c FROM chats").fetchone()["c"]
        if count >= MAX_TABS:
            raise ValueError(f"Limite de {MAX_TABS} abas atingido.")
        cur = conn.execute("INSERT INTO chats (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid


def list_chats():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM chats ORDER BY id").fetchall()]


def rename_chat(chat_id: int, name: str):
    with get_conn() as conn:
        conn.execute("UPDATE chats SET name=? WHERE id=?", (name, chat_id))
        conn.commit()


def delete_chat(chat_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM rag_chunks WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        conn.commit()


def add_message(chat_id: int, role: str, content: str, plot_path: str = None,
                 table_path: str = None, chart_data: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, plot_path, table_path, chart_data) VALUES (?,?,?,?,?,?)",
            (chat_id, role, content, plot_path, table_path, chart_data),
        )
        conn.commit()


def get_history(chat_id: int):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY id", (chat_id,)
        ).fetchall()]


def clear_rag(chat_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM rag_chunks WHERE chat_id=?", (chat_id,))
        conn.commit()


def add_rag_chunks(chat_id: int, chunks: list):
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO rag_chunks (chat_id, content) VALUES (?,?)",
            [(chat_id, c) for c in chunks],
        )
        conn.commit()


def get_rag_chunks(chat_id: int):
    with get_conn() as conn:
        return [r["content"] for r in conn.execute(
            "SELECT content FROM rag_chunks WHERE chat_id=?", (chat_id,)
        ).fetchall()]