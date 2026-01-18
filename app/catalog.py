import aiosqlite
from typing import Any, Optional
from .config import settings


async def add_product(product: dict[str, Any]) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO products(sku,title,description,color,size,price,currency,url,photo_url)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
              title=excluded.title,
              description=excluded.description,
              color=excluded.color,
              size=excluded.size,
              price=excluded.price,
              currency=excluded.currency,
              url=excluded.url,
              photo_url=excluded.photo_url
            """,
            (
                product["sku"],
                product.get("title", ""),
                product.get("description", ""),
                product.get("color", ""),
                product.get("size", ""),
                float(product.get("price", 0) or 0),
                product.get("currency", "RUB"),
                product.get("url", ""),
                product.get("photo_url", ""),
            ),
        )
        await db.commit()


async def search_products(
    query: str = "",
    color: Optional[str] = None,
    size: Optional[str] = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []

    if query:
        where.append("(title LIKE ? OR description LIKE ? OR sku LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q])

    if color:
        where.append("(color LIKE ?)")
        params.append(f"%{color}%")

    if size:
        where.append("(size LIKE ?)")
        params.append(f"%{size}%")

    sql = "SELECT sku,title,description,color,size,price,currency,url,photo_url FROM products"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()

    out = []
    for r in rows:
        out.append(
            dict(
                sku=r[0],
                title=r[1],
                description=r[2],
                color=r[3],
                size=r[4],
                price=r[5],
                currency=r[6],
                url=r[7],
                photo_url=r[8],
            )
        )
    return out
