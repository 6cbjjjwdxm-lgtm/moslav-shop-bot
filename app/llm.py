import json
from typing import Any

from openai import AsyncOpenAI

from .config import settings
from .catalog import search_products
from .db import create_order


SYSTEM_PROMPT = """
Ты — менеджер Telegram-магазина одежды.
Твой стиль: дружелюбно, по делу, без давления, как живой человек.

Цели:
1) Аккуратно выяснить потребность: для кого, стиль/повод, бюджет, цвет, предпочтения по посадке/материалу, сроки.
2) Подобрать варианты из каталога (не выдумывать товары).
3) Довести до покупки: уточнить артикул/цвет/размер, предложить 1-2 лучших варианта, закрыть возражения.
4) Обязательно спросить удобный способ доставки/получения и сроки (курьер/ПВЗ/самовывоз и т.п.).
5) Если пользователь готов покупать — зафиксировать намерение заказа.

Ограничения:
- Если нужных товаров нет — честно сказать и уточнить альтернативы.
- Подбор размера пока НЕ реализован: можно только спросить параметры и сообщить, что точный расчет добавим позже.
""".strip()


client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Ищет товары в каталоге магазина по текстовому запросу/цвету/размеру.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "color": {"type": "string"},
                    "size": {"type": "string"},
                    "limit": {"type": "integer", "default": 6},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order_intent",
            "description": "Создает черновик заказа (намерение покупки) с выбранными товарами и пожеланиями пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "color": {"type": "string"},
                                "size": {"type": "string"},
                                "qty": {"type": "integer", "default": 1},
                            },
                            "required": ["sku"],
                        },
                    },
                    "delivery_preference": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["user_id", "items"],
            },
        },
    },
]


def _dump_item(x: Any) -> Any:
    # openai-python часто возвращает pydantic-модели; для повторной отправки в input нужен dict
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if hasattr(x, "dict"):
        return x.dict()
    return x


async def _run_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "search_catalog":
        return await search_products(
            query=(args.get("query", "") or ""),
            color=args.get("color") or None,
            size=args.get("size") or None,
            limit=int(args.get("limit", 6) or 6),
        )

    if name == "create_order_intent":
        order_id = await create_order(
            user_id=int(args["user_id"]),
            status="intent",
            payload={
                "items": args.get("items", []),
                "delivery_preference": args.get("delivery_preference", ""),
                "comment": args.get("comment", ""),
            },
        )
        return {"order_id": order_id, "status": "intent"}

    return {"error": f"Unknown tool: {name}"}


async def chat(user_id: int, messages: list[dict[str, Any]]) -> str:
    # Контекст для Responses API: сообщения + потом будем дописывать output и tool outputs
    context: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    for _ in range(3):
        resp = await client.responses.create(
            model=settings.OPENAI_MODEL,
            input=context,
            tools=TOOLS,
        )

        # ВАЖНО: сохраняем output в контекст (там лежат function_call с call_id)
        output_items = [_dump_item(it) for it in (getattr(resp, "output", None) or [])]
        context.extend(output_items)

        # Собираем function_call из output
        tool_calls = [it for it in output_items if isinstance(it, dict) and it.get("type") == "function_call"]

        # Если инструментов не вызвано — это финальный ответ
        text = (getattr(resp, "output_text", None) or "").strip()
        if not tool_calls:
            return text or "Можешь уточнить, что именно ищем: тип одежды, повод и примерный бюджет?"

        # Выполняем все вызовы инструментов и добавляем function_call_output
        for call in tool_calls:
            name = call.get("name")
            args_json = call.get("arguments") or "{}"
            call_id = call.get("call_id")

            try:
                args = json.loads(args_json)
            except json.JSONDecodeError:
                args = {}

            result = await _run_tool(name, args)

            context.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                }
            )

        # Следующая итерация цикла: модель увидит function_call_output и сформирует ответ

    return "Давай уточним пару деталей (повод, цвет, бюджет), и предложу варианты."
