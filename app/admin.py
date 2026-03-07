from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from .db import (
    add_color,
    get_product,
    set_color_active,
    set_product_active,
    set_variant_active,
    toggle_product_sale,
    update_product_price,
)

ADMIN_PHONE_NOTE = "По вопросам закупок по оптовым ценам обращайтесь по тел. 8-903-776-17-47"

router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


SIZES = ["S", "M", "L", "XL", "XXL", "XXXL"]


def _admin_home_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data="adm:add"))
    b.row(InlineKeyboardButton(text="✏️ Редактировать товар", callback_data="adm:edit"))
    return b.as_markup()


def _product_actions_kb(sku: str, is_active: bool, is_sale: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=("🔴 Снять с ленты" if is_active else "🟢 Включить в ленту"),
            callback_data=f"p:active:{sku}:{1 if is_active else 0}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=("🔥 Убрать распродажу" if is_sale else "🔥 Пометить распродажа"),
            callback_data=f"p:sale:{sku}",
        )
    )
    b.row(InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"p:price:{sku}"))
    b.row(InlineKeyboardButton(text="📏 Размеры", callback_data=f"p:sizes:{sku}"))
    b.row(InlineKeyboardButton(text="🎨 Цвета", callback_data=f"p:colors:{sku}"))
    return b.as_markup()


def _render_product_text(p: dict) -> str:
    head = []
    if p.get("is_sale"):
        head.append("🔥 РАСПРОДАЖА")
    head.append(f"Артикул: {p['sku']}")
    head.append(f"Название: {p.get('title','')}")
    head.append(f"Цена: {p.get('price',0)} {p.get('currency','RUB')}")
    head.append(f"Статус: {'АКТИВЕН' if p.get('is_active') else 'НЕ АКТИВЕН'}")
    head.append(f"Пол: {p.get('gender','')}")
    head.append(f"Категория: {p.get('category','')}")
    head.append(f"Сезон: {p.get('season','')}")
    if p.get("insulation"):
        head.append(f"Утеплитель: {p.get('insulation')}")
    if p.get("material"):
        head.append(f"Материал: {p.get('material')}")
    sizes = [x["size"] for x in p.get("sizes", []) if x.get("is_active")]
    colors = [x["color"] for x in p.get("colors", []) if x.get("is_active")]
    head.append("Размеры: " + (", ".join(sizes) if sizes else "—"))
    head.append("Цвета: " + (", ".join(colors) if colors else "—"))
    desc = (p.get("description") or "").strip()
    if desc:
        head.append("")
        head.append(desc)
    return "\n".join(head)


async def send_product_card(chat_id: int, sku: str, bot: Bot) -> None:
    p = await get_product(sku)
    if not p:
        await bot.send_message(chat_id, f"Товар {sku} не найден в базе.")
        return

    text = _render_product_text(p)
    kb = _product_actions_kb(p["sku"], bool(p["is_active"]), bool(p["is_sale"]))

    photos = p.get("photo_file_ids") or []
    if photos:
        # Первое фото с подписью + кнопки
        await bot.send_photo(chat_id, photos[0], caption=text, reply_markup=kb)
        # Остальные фото без подписи
        for fid in photos[1:3]:
            await bot.send_photo(chat_id, fid)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)


@dataclass
class AdminPending:
    mode: str
    sku: str


# Простое in-memory ожидание ввода (цена/цвет) — на каркас.
# На будущее лучше перевести в FSM+state storage.
_PENDING: dict[int, AdminPending] = {}


def _parse_sku_cmd(text: str) -> tuple[str, str]:
    t = (text or "").strip()
    if not t:
        return "", ""
    parts = t.split(maxsplit=1)
    sku = parts[0].strip()
    cmd = parts[1].strip().lower() if len(parts) == 2 else ""
    return sku, cmd


@router.message(Command("start"))
async def admin_start(m: Message):
    if not m.from_user:
        return
    if m.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(m.from_user.id):
        return
    await m.answer("Админ-меню:", reply_markup=_admin_home_kb())


@router.callback_query(F.data == "adm:edit")
async def adm_edit(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.message.answer("Пришли артикул товара (SKU), и я покажу карточку.")
    await cb.answer()


@router.message(F.text)
async def admin_sku_shortcuts(m: Message, bot: Bot):
    if not m.from_user or m.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(m.from_user.id):
        return

    user_id = m.from_user.id

    # Если ждём ввод (цена/цвет)
    pend = _PENDING.get(user_id)
    if pend:
        if pend.mode == "price":
            try:
                price = float((m.text or "").replace(",", ".").strip())
            except ValueError:
                return await m.answer("Цена должна быть числом. Например: 5990")
            await update_product_price(pend.sku, price)
            _PENDING.pop(user_id, None)
            await m.answer("Ок, цена обновлена.")
            await send_product_card(m.chat.id, pend.sku, bot)
            return

        if pend.mode == "add_color":
            color = (m.text or "").strip()
            if not color:
                return await m.answer("Напиши цвет текстом, например: черный")
            await add_color(pend.sku, color)
            _PENDING.pop(user_id, None)
            await m.answer("Ок, цвет добавлен.")
            await send_product_card(m.chat.id, pend.sku, bot)
            return

    # Сокращения по SKU
    sku, cmd = _parse_sku_cmd(m.text or "")
    if not sku:
        return

    p = await get_product(sku)
    if not p:
        return  # пусть обработает обычный пользовательский диалог

    if "продан" in cmd:
        await set_product_active(sku, False)
        await m.answer(f"Ок, {sku} снят с ленты (в базе сохранён).")
        await send_product_card(m.chat.id, sku, bot)
        return

    # просто SKU -> показать карточку + действия
    await send_product_card(m.chat.id, sku, bot)


@router.callback_query(F.data.startswith("p:active:"))
async def cb_toggle_active(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku, was_active = (cb.data or "").split(":", 3)
    # was_active: "1" если был активен, значит выключаем
    new_active = False if was_active == "1" else True
    await set_product_active(sku, new_active)

    await cb.answer("Готово")
    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data.startswith("p:sale:"))
async def cb_toggle_sale(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku = (cb.data or "").split(":", 2)
    await toggle_product_sale(sku)

    await cb.answer("Готово")
    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data.startswith("p:price:"))
async def cb_price(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku = (cb.data or "").split(":", 2)
    _PENDING[cb.from_user.id] = AdminPending(mode="price", sku=sku)
    await cb.message.answer(f"Введи новую цену для {sku} одним числом (например 5990).")
    await cb.answer()


def _sizes_kb(sku: str, active_sizes: set[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in SIZES:
        mark = "✅" if s in active_sizes else "☑️"
        b.row(InlineKeyboardButton(text=f"{mark} {s}", callback_data=f"sz:t:{sku}:{s}"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:back:{sku}"))
    return b.as_markup()


@router.callback_query(F.data.startswith("p:sizes:"))
async def cb_sizes(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku = (cb.data or "").split(":", 2)
    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)
    active_sizes = {x["size"] for x in p.get("sizes", []) if x.get("is_active")}
    await cb.message.edit_text(
        f"Размеры для {sku} (кликай чтобы включать/выключать):",
        reply_markup=_sizes_kb(sku, active_sizes),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sz:t:"))
async def cb_size_toggle(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku, size = (cb.data or "").split(":", 3)
    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)

    active_sizes = {x["size"] for x in p.get("sizes", []) if x.get("is_active")}
    new_active = size not in active_sizes
    await set_variant_active(sku, size, new_active)

    p2 = await get_product(sku)
    active_sizes2 = {x["size"] for x in p2.get("sizes", []) if x.get("is_active")}
    await cb.message.edit_reply_markup(reply_markup=_sizes_kb(sku, active_sizes2))
    await cb.answer("Ок")


def _colors_kb(sku: str, colors: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in colors:
        color = c["color"]
        active = bool(c["is_active"])
        mark = "✅" if active else "☑️"
        b.row(InlineKeyboardButton(text=f"{mark} {color}", callback_data=f"cl:t:{sku}:{color}"))
    b.row(InlineKeyboardButton(text="➕ Добавить цвет", callback_data=f"cl:add:{sku}"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:back:{sku}"))
    return b.as_markup()


@router.callback_query(F.data.startswith("p:colors:"))
async def cb_colors(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku = (cb.data or "").split(":", 2)
    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)
    await cb.message.edit_text(
        f"Цвета для {sku}:",
        reply_markup=_colors_kb(sku, p.get("colors", [])),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cl:add:"))
async def cb_color_add(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku = (cb.data or "").split(":", 2)
    _PENDING[cb.from_user.id] = AdminPending(mode="add_color", sku=sku)
    await cb.message.answer(f"Напиши новый цвет для {sku} (например: черный).")
    await cb.answer()


@router.callback_query(F.data.startswith("cl:t:"))
async def cb_color_toggle(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, sku, color = (cb.data or "").split(":", 3)
    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)

    colors = {x["color"]: bool(x["is_active"]) for x in p.get("colors", [])}
    cur_active = colors.get(color, True)
    await set_color_active(sku, color, not cur_active)

    p2 = await get_product(sku)
    await cb.message.edit_reply_markup(reply_markup=_colors_kb(sku, p2.get("colors", [])))
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("p:back:"))
async def cb_back(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    _, _, sku = (cb.data or "").split(":", 2)
    await cb.answer()
    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)
