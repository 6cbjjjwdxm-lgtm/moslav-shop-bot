from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from .db import get_conversation, upsert_conversation
from .llm import chat

router = Router()


@router.message(Command("start"))
async def start(m: Message):
    if m.chat.type != ChatType.PRIVATE:
        return

    await m.answer(
        "Привет! Я менеджер магазина одежды.\n"
        "Напиши, что ищешь (например: худи на зиму, черное, до 6000), и я подберу варианты."
    )


@router.message(Command("reset"))
async def reset(m: Message):
    if m.chat.type != ChatType.PRIVATE:
        return

    if not m.from_user:
        return

    await upsert_conversation(m.from_user.id, [])
    await m.answer("Диалог сброшен. Напиши, что ищешь.")


@router.message()
async def any_text(m: Message):
    if m.chat.type != ChatType.PRIVATE:
        return

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


