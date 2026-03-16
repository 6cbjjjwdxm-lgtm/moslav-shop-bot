from typing import Any, Optional

import aiosqlite

from .config import settings


async def search_products(
    query: str = "",
    color: Optional[str] = None,
    size: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
    season: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    where = ["p.is_active = 1"]
    params: list[Any] = []

    if query:
        where.append("(p.title LIKE ? OR p.description LIKE ? OR p.sku LIKE ? OR p.category LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q, q])

    if color:
        where.append(
            "EXISTS (SELECT 1 FROM product_colors pc WHERE pc.sku = p.sku AND pc.is_active = 1 AND pc.color LIKE ?)"
        )
        params.append(f"%{color}%")

    if size:
        where.append(
            "EXISTS (SELECT 1 FROM product_variants pv WHERE pv.sku = p.sku AND pv.is_active = 1 AND pv.size = ?)"
        )
        params.append(size.strip().upper())

    if gender:
        where.append("p.gender = ?")
        params.append(gender.strip())

    if category:
        where.append("p.category LIKE ?")
        params.append(f"%{category}%")

    if season:
        where.append("p.season LIKE ?")
        params.append(f"%{season}%")

    if min_price is not None:
        where.append("p.price >= ?")
        params.append(float(min_price))

    if max_price is not None:
        where.append("p.price <= ?")
        params.append(float(max_price))

    sql = """
        SELECT p.sku, p.title, p.description, p.gender, p.category, p.season,
               p.insulation, p.material, p.price, p.currency, p.is_sale
        FROM products p
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.is_sale DESC, p.created_at DESC LIMIT ?"
    params.append(int(limit))

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()

    results = []
    for r in rows:
        sku = r[0]

        async with aiosqlite.connect(settings.DB_PATH) as db:
            cur = await db.execute(
                "SELECT size FROM product_variants WHERE sku = ? AND is_active = 1 ORDER BY size",
                (sku,),
            )
            sizes = [row[0] for row in await cur.fetchall()]

            cur = await db.execute(
                "SELECT color FROM product_colors WHERE sku = ? AND is_active = 1 ORDER BY color",
                (sku,),
            )
            colors = [row[0] for row in await cur.fetchall()]

        results.append({
            "sku": sku,
            "title": r[1],
            "description": r[2],
            "gender": r[3],
            "category": r[4],
            "season": r[5],
            "insulation": r[6],
            "material": r[7],
            "price": r[8],
            "currency": r[9],
            "is_sale": bool(r[10]),
            "sizes": sizes,
            "colors": colors,
        })

    return results
