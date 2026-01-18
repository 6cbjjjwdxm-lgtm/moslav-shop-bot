import json
import time
import aiosqlite
from typing import Any, Optional
from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  sku TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  color TEXT DEFAULT '',
  size TEXT DEFAULT '',
  price REAL DEFAULT 0,
  currency TEXT DEFAULT 'RUB',
  url TEXT DEFAULT '',
  photo_url TEXT DEFAULT '',
  created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS conversations (
  user_id INTEGER PRIMARY KEY,
  messages_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_conversation(user_id: int, messages: list[dict[str, Any]]) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO conversations(user_id, messages_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              messages_json=excluded.messages_json,
              updated_at=excluded.updated_at
            """,
            (user_id, json.dumps(messages, ensure_ascii=False), now),
        )
        await db.commit()


async def get_conversation(user_id: int) -> Optional[list[dict[str, Any]]]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT messages_json FROM conversations WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None


async def create_order(user_id: int, status: str, payload: dict[str, Any]) -> int:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders(user_id, status, payload_json, created_at) VALUES(?,?,?,?)",
            (user_id, status, json.dumps(payload, ensure_ascii=False), now),
        )
        await db.commit()
        return int(cur.lastrowid)
