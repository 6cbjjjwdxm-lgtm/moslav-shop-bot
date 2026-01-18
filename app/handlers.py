from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .config import settings
from .db import get_conversation, upsert_conversation
from .catalog import add_product
from .llm import chat

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


@router.message(Command("start"))
async def start(m: Message):
    await m.answer(
        "Привет! Я менеджер магазина одежды.\n"
        "Напиши, что ищешь (например: худи на зиму, черное, до 6000), и я подберу варианты."
    )


@router.message(Command("add"))
async def admin_add(m: Message):
    if not m.from_user or not _is_admin(m.from_user.id):
        return await m.answer("Команда доступна только админам.")

    # /add SKU | Название | Цвет | Размер | Цена | Описание (опц.)
    text = (m.text or "").replace("/add", "", 1).strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 5:
        return await m.answer("Формат:\n/add SKU | Название | Цвет | Размер | Цена | Описание (опц.)")

    sku, title, color, size, price = parts[:5]
    description = parts[5] if len(parts) >= 6 else ""

    await add_product(
        {
            "sku": sku,
            "title": title,
            "color": color,
            "size": size,
            "price": float(price.replace(",", ".")),
            "description": description,
            "currency": "RUB",
        }
    )

    await m.answer(f"Ок, товар {sku} добавлен/обновлён.")


@router.message(Command("reset"))
async def reset(m: Message):
    if not m.from_user:
        return
    await upsert_conversation(m.from_user.id, [])
    await m.answer("Диалог сброшен. Напиши, что ищешь.")


@router.message()
async def any_text(m: Message):
    if not m.from_user or not m.text:
        return

    user_id = m.from_user.id
    history = await get_conversation(user_id) or []
    history.append({"role": "user", "content": m.text})
    history = history[-20:]

    reply = await chat(user_id=user_id, messages=history)

    history.append({"role": "assistant", "content": reply})
    await upsert_conversation(user_id, history)

    await m.answer(reply)
