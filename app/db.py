import json
import secrets
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
  created_at INTEGER NOT NULL
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

CREATE TABLE IF NOT EXISTS sales_sessions (
  user_id INTEGER PRIMARY KEY,
  sku TEXT DEFAULT '',
  stage TEXT NOT NULL DEFAULT 'new_chat',
  psychotype TEXT DEFAULT '',
  psychotype_conf REAL DEFAULT 0,
  context_json TEXT DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL,
  sku TEXT NOT NULL,
  title TEXT DEFAULT '',
  price REAL DEFAULT 0,
  currency TEXT DEFAULT 'RUB',
  size TEXT DEFAULT '',
  color TEXT DEFAULT '',
  customer_name TEXT DEFAULT '',
  customer_phone TEXT DEFAULT '',
  comment TEXT DEFAULT '',
  psychotype TEXT DEFAULT '',
  payment_url TEXT DEFAULT '',
  carrier TEXT DEFAULT '',
  tracking_number TEXT DEFAULT '',
  stage TEXT NOT NULL DEFAULT 'waiting_payment',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
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
        cur = await db.execute(
            "SELECT messages_json FROM conversations WHERE user_id=?",
            (user_id,),
        )
        row = await cur.fetchone()

    if not row:
        return None

    try:
        return json.loads(row[0])
    except Exception:
        return None


# -------- Legacy orders --------
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
            FROM products
            WHERE sku=?
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

    return {
        "sku": row[0],
        "title": row[1],
        "description": row[2],
        "gender": row[3],
        "category": row[4],
        "season": row[5],
        "insulation": row[6],
        "material": row[7],
        "price": row[8],
        "currency": row[9],
        "is_active": bool(row[10]),
        "is_sale": bool(row[11]),
        "created_at": row[12],
        "updated_at": row[13],
        "sizes": [
            {"size": v[0], "stock": v[1], "is_active": bool(v[2])}
            for v in variants
        ],
        "colors": [
            {"color": c[0], "is_active": bool(c[1])}
            for c in colors
        ],
        "photo_file_ids": [p[0] for p in photos],
    }


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
            INSERT INTO products(
              sku,title,description,gender,category,season,insulation,material,
              price,currency,is_active,is_sale,created_at,updated_at
            )
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


# -------- Channel publications --------
async def save_product_publication(sku: str, chat_id: str, message_id: int) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO product_publications(sku, chat_id, message_id, created_at)
            VALUES(?,?,?,?)
            """,
            ((sku or "").strip(), str(chat_id).strip(), int(message_id), now),
        )
        await db.commit()


async def get_product_publications(sku: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, sku, chat_id, message_id, created_at
            FROM product_publications
            WHERE sku=?
            ORDER BY id
            """,
            ((sku or "").strip(),),
        )
        rows = await cur.fetchall()

    return [
        {
            "id": row[0],
            "sku": row[1],
            "chat_id": row[2],
            "message_id": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


async def clear_product_publications(sku: str) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "DELETE FROM product_publications WHERE sku=?",
            ((sku or "").strip(),),
        )
        await db.commit()


# -------- Sales sessions --------
async def get_sales_session(user_id: int) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT user_id, sku, stage, psychotype, psychotype_conf, context_json, created_at, updated_at
            FROM sales_sessions
            WHERE user_id=?
            """,
            (user_id,),
        )
        row = await cur.fetchone()

    if not row:
        return None

    try:
        context = json.loads(row[5] or "{}")
    except Exception:
        context = {}

    return {
        "user_id": row[0],
        "sku": row[1],
        "stage": row[2],
        "psychotype": row[3],
        "psychotype_conf": row[4],
        "context": context,
        "created_at": row[6],
        "updated_at": row[7],
    }


async def upsert_sales_session(
    *,
    user_id: int,
    sku: str = "",
    stage: str = "new_chat",
    psychotype: str = "",
    psychotype_conf: float = 0,
    context: dict[str, Any] | None = None,
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO sales_sessions(
              user_id, sku, stage, psychotype, psychotype_conf, context_json, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              sku=excluded.sku,
              stage=excluded.stage,
              psychotype=excluded.psychotype,
              psychotype_conf=excluded.psychotype_conf,
              context_json=excluded.context_json,
              updated_at=excluded.updated_at
            """,
            (
                user_id,
                (sku or "").strip(),
                stage,
                (psychotype or "").strip(),
                float(psychotype_conf or 0),
                json.dumps(context or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()


async def clear_sales_session(user_id: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("DELETE FROM sales_sessions WHERE user_id=?", (user_id,))
        await db.commit()


# -------- Sales orders --------
def _make_order_no() -> str:
    ts = time.strftime("%Y%m%d")
    suffix = secrets.token_hex(2).upper()
    return f"MS-{ts}-{suffix}"


async def create_sales_order(
    *,
    user_id: int,
    sku: str,
    title: str = "",
    price: float = 0,
    currency: str = "RUB",
    size: str = "",
    color: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    comment: str = "",
    psychotype: str = "",
    payment_url: str = "",
    stage: str = "waiting_payment",
) -> dict[str, Any]:
    now = int(time.time())
    order_no = _make_order_no()

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO sales_orders(
              order_no, user_id, sku, title, price, currency, size, color,
              customer_name, customer_phone, comment, psychotype,
              payment_url, carrier, tracking_number, stage, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                order_no,
                user_id,
                (sku or "").strip(),
                (title or "").strip(),
                float(price or 0),
                (currency or "RUB").strip(),
                (size or "").strip(),
                (color or "").strip(),
                (customer_name or "").strip(),
                (customer_phone or "").strip(),
                (comment or "").strip(),
                (psychotype or "").strip(),
                (payment_url or "").strip(),
                "",
                "",
                stage,
                now,
                now,
            ),
        )
        await db.commit()
        order_id = int(cur.lastrowid)

    return {
        "id": order_id,
        "order_no": order_no,
        "user_id": user_id,
        "sku": sku,
        "title": title,
        "price": float(price or 0),
        "currency": currency,
        "size": size,
        "color": color,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "comment": comment,
        "psychotype": psychotype,
        "payment_url": payment_url,
        "carrier": "",
        "tracking_number": "",
        "stage": stage,
        "created_at": now,
        "updated_at": now,
    }


async def get_sales_order_by_no(order_no: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, order_no, user_id, sku, title, price, currency, size, color,
                   customer_name, customer_phone, comment, psychotype,
                   payment_url, carrier, tracking_number, stage, created_at, updated_at
            FROM sales_orders
            WHERE order_no=?
            """,
            ((order_no or "").strip(),),
        )
        row = await cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "order_no": row[1],
        "user_id": row[2],
        "sku": row[3],
        "title": row[4],
        "price": row[5],
        "currency": row[6],
        "size": row[7],
        "color": row[8],
        "customer_name": row[9],
        "customer_phone": row[10],
        "comment": row[11],
        "psychotype": row[12],
        "payment_url": row[13],
        "carrier": row[14],
        "tracking_number": row[15],
        "stage": row[16],
        "created_at": row[17],
        "updated_at": row[18],
    }


async def update_sales_order_stage(order_no: str, stage: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "UPDATE sales_orders SET stage=?, updated_at=? WHERE order_no=?",
            (stage, now, (order_no or "").strip()),
        )
        await db.commit()


async def set_sales_order_tracking(order_no: str, carrier: str, tracking_number: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            UPDATE sales_orders
            SET carrier=?, tracking_number=?, stage='shipped', updated_at=?
            WHERE order_no=?
            """,
            (
                (carrier or "").strip(),
                (tracking_number or "").strip(),
                now,
                (order_no or "").strip(),
            ),
        )
        await db.commit()

