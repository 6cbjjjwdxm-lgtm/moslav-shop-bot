import json
import time
from typing import Any, Optional

import aiosqlite

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  sku TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  gender TEXT DEFAULT '',
  category TEXT DEFAULT '',
  season TEXT DEFAULT '',
  insulation TEXT DEFAULT '',
  material TEXT DEFAULT '',
  price REAL DEFAULT 0,
  currency TEXT DEFAULT 'RUB',
  is_active INTEGER DEFAULT 1,
  is_sale INTEGER DEFAULT 0,
  created_at INTEGER DEFAULT (strftime('%s','now')),
  updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS product_variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL,
  size TEXT NOT NULL,
  stock INTEGER DEFAULT 0,
  is_active INTEGER DEFAULT 1,
  UNIQUE(sku, size)
);

CREATE TABLE IF NOT EXISTS product_colors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL,
  color TEXT NOT NULL,
  is_active INTEGER DEFAULT 1,
  UNIQUE(sku, color)
);

CREATE TABLE IF NOT EXISTS product_photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL,
  file_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  message_id INTEGER NOT NULL,
  created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  psychotype TEXT DEFAULT '',
  psychotype_conf REAL DEFAULT 0,
  notes_json TEXT DEFAULT '{}',
  last_delivery_pref TEXT DEFAULT '',
  updated_at INTEGER NOT NULL
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


# -------- Conversations --------
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


# -------- Products --------
async def get_product(sku: str) -> Optional[dict[str, Any]]:
    sku = (sku or "").strip()
    if not sku:
        return None

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT sku,title,description,gender,category,season,insulation,material,price,currency,is_active,is_sale,created_at,updated_at
            FROM products WHERE sku=?
            """,
            (sku,),
        )
        row = await cur.fetchone()
        if not row:
            return None

        cur = await db.execute(
            "SELECT size,stock,is_active FROM product_variants WHERE sku=? ORDER BY size",
            (sku,),
        )
        variants = await cur.fetchall()

        cur = await db.execute(
            "SELECT color,is_active FROM product_colors WHERE sku=? ORDER BY color",
            (sku,),
        )
        colors = await cur.fetchall()

        cur = await db.execute(
            "SELECT file_id FROM product_photos WHERE sku=? ORDER BY id",
            (sku,),
        )
        photos = await cur.fetchall()

    return dict(
        sku=row[0],
        title=row[1],
        description=row[2],
        gender=row[3],
        category=row[4],
        season=row[5],
        insulation=row[6],
        material=row[7],
        price=row[8],
        currency=row[9],
        is_active=bool(row[10]),
        is_sale=bool(row[11]),
        created_at=row[12],
        updated_at=row[13],
        sizes=[dict(size=v[0], stock=v[1], is_active=bool(v[2])) for v in variants],
        colors=[dict(color=c[0], is_active=bool(c[1])) for c in colors],
        photo_file_ids=[p[0] for p in photos],
    )


async def set_product_active(sku: str, active: bool) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "UPDATE products SET is_active=?, updated_at=? WHERE sku=?",
            (1 if active else 0, now, sku),
        )
        await db.commit()


async def toggle_product_sale(sku: str) -> Optional[bool]:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT is_sale FROM products WHERE sku=?", (sku,))
        row = await cur.fetchone()
        if not row:
            return None
        new_val = 0 if int(row[0] or 0) else 1
        await db.execute(
            "UPDATE products SET is_sale=?, updated_at=? WHERE sku=?",
            (new_val, now, sku),
        )
        await db.commit()
        return bool(new_val)


async def update_product_price(sku: str, price: float) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "UPDATE products SET price=?, updated_at=? WHERE sku=?",
            (float(price), now, sku),
        )
        await db.commit()


async def upsert_product(
    *,
    sku: str,
    title: str,
    description: str,
    gender: str,
    category: str,
    season: str,
    insulation: str,
    material: str,
    price: float,
    currency: str = "RUB",
    is_active: bool = True,
    is_sale: bool = False,
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO products(sku,title,description,gender,category,season,insulation,material,price,currency,is_active,is_sale,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
              title=excluded.title,
              description=excluded.description,
              gender=excluded.gender,
              category=excluded.category,
              season=excluded.season,
              insulation=excluded.insulation,
              material=excluded.material,
              price=excluded.price,
              currency=excluded.currency,
              is_active=excluded.is_active,
              is_sale=excluded.is_sale,
              updated_at=excluded.updated_at
            """,
            (
                sku,
                title,
                description,
                gender,
                category,
                season,
                insulation,
                material,
                float(price),
                currency,
                1 if is_active else 0,
                1 if is_sale else 0,
                now,
                now,
            ),
        )
        await db.commit()


async def set_variant_active(sku: str, size: str, active: bool) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO product_variants(sku,size,is_active)
            VALUES(?,?,?)
            ON CONFLICT(sku,size) DO UPDATE SET
              is_active=excluded.is_active
            """,
            (sku, size, 1 if active else 0),
        )
        await db.commit()


async def add_color(sku: str, color: str) -> None:
    color = (color or "").strip()
    if not color:
        return
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO product_colors(sku,color,is_active)
            VALUES(?,?,1)
            ON CONFLICT(sku,color) DO UPDATE SET
              is_active=1
            """,
            (sku, color),
        )
        await db.commit()


async def set_color_active(sku: str, color: str, active: bool) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO product_colors(sku,color,is_active)
            VALUES(?,?,?)
            ON CONFLICT(sku,color) DO UPDATE SET
              is_active=excluded.is_active
            """,
            (sku, color, 1 if active else 0),
        )
        await db.commit()


async def add_photo_file_id(sku: str, file_id: str) -> None:
    if not (sku and file_id):
        return
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO product_photos(sku,file_id) VALUES(?,?)",
            (sku, file_id),
        )
        await db.commit()


# -------- Product publications --------
async def save_product_publication(sku: str, chat_id: str, message_id: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO product_publications(sku, chat_id, message_id, created_at)
            VALUES(?,?,?,?)
            """,
            (sku, str(chat_id), int(message_id), int(time.time())),
        )
        await db.commit()


async def get_product_publications(sku: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT sku, chat_id, message_id, created_at
            FROM product_publications
            WHERE sku=?
            ORDER BY id
            """,
            (sku,),
        )
        rows = await cur.fetchall()

    return [
        {
            "sku": row[0],
            "chat_id": row[1],
            "message_id": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]


async def clear_product_publications(sku: str) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "DELETE FROM product_publications WHERE sku=?",
            (sku,),
        )
        await db.commit()

async def update_product_description(sku: str, description: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "UPDATE products SET description=?, updated_at=? WHERE sku=?",
            ((description or "").strip(), now, sku),
        )
        await db.commit()

async def delete_product(sku: str) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("DELETE FROM product_publications WHERE sku=?", (sku,))
        await db.execute("DELETE FROM product_photos WHERE sku=?", (sku,))
        await db.execute("DELETE FROM product_colors WHERE sku=?", (sku,))
        await db.execute("DELETE FROM product_variants WHERE sku=?", (sku,))
        await db.execute("DELETE FROM products WHERE sku=?", (sku,))
        await db.commit()

