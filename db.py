"""資料庫連線與各資料表初始化。

集中管理 SQLite 連線與所有 CREATE TABLE，讓其他模組（auth / conversations / rag）
統一從這裡取得資料庫連線，不必各自重複連線設定。
"""
import os
import sqlite3

DATA_DIR    = os.environ.get("DATA_DIR", os.path.dirname(__file__))
DB_PATH     = os.path.join(DATA_DIR, "pesticides.db")
CHROMA_PATH = os.path.join(DATA_DIR, "chroma_db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_tables():
    """帳號與登入憑證資料表。"""
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


def init_conversation_table():
    """對話紀錄資料表（整筆對話以 JSON 存一欄，綁定 username）。"""
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        title TEXT NOT NULL,
        messages TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(username)')
    conn.commit()
    conn.close()
