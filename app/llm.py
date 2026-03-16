import json
from typing import Any

from openai import AsyncOpenAI

from .config import settings
from .catalog import search_products
from .db import create_order

SYSTEM_PROMPT = """
Ты — менеджер Telegram-магазина одежды MOSLAV.
Твой стиль: дружелюбно, по делу, без давления, как живой человек.
Отвечай коротко (2-4 предложения), без списков и маркдауна, если только не описываешь товар.

Цели:
1) Аккуратно выяснить потребность: для кого, стиль/повод, бюджет, цвет, предпочтения по посадке/материалу, сроки.
2) Подобрать варианты из каталога (не выдумывать товары). Если ничего не нашлось — честно сказать.
3) Довести до покупки: уточнить артикул/цвет/размер, предложить 1-2 лучших варианта, закрыть возражения мягко.
4) Спросить удобный способ доставки/получения и сроки.
5) Если пользователь готов покупать — зафиксировать намерение заказа.

Ограничения:
- Не придумывай товары, которых нет в каталоге.
- Не обсуждай темы, не связанные с магазином — вежливо верни к покупкам.
- Не давай медицинских советов.
""".strip()


SALES_SYSTEM_PROMPT_TEMPLATE = """
Ты — менеджер Telegram-магазина одежды MOSLAV.
Ты ведёшь личный диалог с покупателем, помогаешь выбрать и оформить заказ.
Общайся как живой человек, а не шаблонный бот. Будь конкретен, дружелюбен, без давления.

=== СТИЛЬ ОБЩЕНИЯ ===
Психотип покупателя: {psychotype}
Уверенность: {psychotype_conf:.0%}
Тон: {tone}
Длина ответов: {length}
Аргументы: {arguments}
Стиль закрытия: {closing}
Работа с возражениями: {objections}

=== КОНТЕКСТ ЛИДА ===
{lead_context}

=== ТЕКУЩИЙ ТОВАР ===
{product_info}

=== СТАДИЯ ВОРОНКИ ===
{stage_description}

=== РАЗМЕРНАЯ РЕКОМЕНДАЦИЯ ===
{sizing_info}

=== ПРАВИЛА ===
1) Отвечай коротко (2-5 предложений), как в мессенджере, не как в email.
2) Не используй маркдаун-заголовки и списки — пиши обычным текстом.
3) Если покупатель сомневается — работай с возражением по стилю психотипа, не дави.
4) Если покупатель готов оформить — мягко предложи перейти к оформлению (размер, цвет, имя, телефон).
5) Если нужен размер — задай вопрос о росте/весе/параметрах.
6) Не выдумывай товары. Используй только данные из каталога.
7) Если покупатель пишет не по теме — вежливо верни к покупкам.
8) Всегда фиксируй следующий шаг в конце ответа (вопрос, предложение, CTA).
""".strip()


client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

TOOLS = [
    {
        "type": "function",
        "name": "search_catalog",
        "description": "Ищет товары в каталоге магазина по текстовому запросу, цвету, размеру, полу, категории, сезону, ценовому диапазону.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Текстовый запрос для поиска"},
                "color": {"type": "string", "description": "Фильтр по цвету"},
                "size": {"type": "string", "description": "Фильтр по размеру (S/M/L/XL/XXL/XXXL)"},
                "gender": {"type": "string", "description": "Пол: male или female"},
                "category": {"type": "string", "description": "Категория товара"},
                "season": {"type": "string", "description": "Сезон: summer, autumn, winter, euro_winter"},
                "min_price": {"type": "number", "description": "Минимальная цена"},
                "max_price": {"type": "number", "description": "Максимальная цена"},
                "limit": {"type": "integer", "default": 6},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "create_order_intent",
        "description": "Создает черновик заказа (намерение покупки) с выбранными товарами и пожеланиями.",
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
]


def _dump_item(x: Any) -> Any:
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
            gender=args.get("gender") or None,
            category=args.get("category") or None,
            season=args.get("season") or None,
            min_price=args.get("min_price"),
            max_price=args.get("max_price"),
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
    context: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    for _ in range(3):
        resp = await client.responses.create(
            model=settings.OPENAI_MODEL,
            input=context,
            tools=TOOLS,
        )

        output_items = [_dump_item(it) for it in (getattr(resp, "output", None) or [])]
        context.extend(output_items)

        tool_calls = [it for it in output_items if isinstance(it, dict) and it.get("type") == "function_call"]

        text = (getattr(resp, "output_text", None) or "").strip()
        if not tool_calls:
            return text or "Можешь уточнить, что именно ищем: тип одежды, повод и примерный бюджет?"

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

    return "Давай уточним пару деталей (повод, цвет, бюджет), и предложу варианты."


async def sales_chat(
    user_id: int,
    messages: list[dict[str, Any]],
    system_prompt: str,
) -> str:
    """LLM chat for the sales funnel with a custom system prompt."""
    context: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}] + list(messages)

    for _ in range(3):
        resp = await client.responses.create(
            model=settings.OPENAI_MODEL,
            input=context,
            tools=TOOLS,
        )

        output_items = [_dump_item(it) for it in (getattr(resp, "output", None) or [])]
        context.extend(output_items)

        tool_calls = [it for it in output_items if isinstance(it, dict) and it.get("type") == "function_call"]

        text = (getattr(resp, "output_text", None) or "").strip()
        if not tool_calls:
            return text or "Расскажите подробнее, что ищете, и я помогу подобрать."

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

    return "Давайте уточним детали и подберём идеальный вариант."
