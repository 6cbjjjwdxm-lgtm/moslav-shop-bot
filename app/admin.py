from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from .db import (
    add_color,
    add_photo_file_id,
    get_product,
    set_color_active,
    set_product_active,
    set_variant_active,
    toggle_product_sale,
    update_product_price,
    upsert_product,
)

router = Router(name="admin")

MY_ADMIN_ID = 123456789  # сюда поставь свой Telegram ID

ADMIN_FILTER = F.from_user.id == MY_ADMIN_ID

def _is_admin(user_id: int) -> bool:
    return user_id == MY_ADMIN_ID

PRIVATE_FILTER = F.chat.type == ChatType.PRIVATE

WHOLESALE_NOTE = "По вопросам закупок по оптовым ценам обращайтесь по тел. 8-903-776-17-47"

GENDERS = [
    ("Мужской", "male"),
    ("Женский", "female"),
]

CATEGORIES = [
    ("Брюки", "pants"),
    ("Толстовка", "hoodie"),
    ("Футболка", "tshirt"),
    ("Шорты", "shorts"),
    ("Безрукавка", "vest"),
    ("Анарак", "anorak"),
    ("Куртка", "jacket"),
    ("Спортивный костюм", "tracksuit"),
]

SEASONS = [
    ("Лето", "summer"),
    ("Осень", "autumn"),
    ("Зима", "winter"),
    ("Еврозима", "euro_winter"),
]

INSULATIONS = [
    ("Нет", ""),
    ("Тинсулейт", "thinsulate"),
    ("Холофайбер", "holofiber"),
    ("Пух", "down"),
]

SIZES = ["S", "M", "L", "XL", "XXL", "XXXL"]


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


def _new_token() -> str:
    return secrets.token_hex(4)


def _normalize_sku(text: str) -> str:
    return (text or "").strip()


def _ensure_note(description: str) -> str:
    desc = (description or "").strip()
    if WHOLESALE_NOTE.lower() in desc.lower():
        return desc
    if desc:
        return f"{desc}\n\n{WHOLESALE_NOTE}"
    return WHOLESALE_NOTE


def _admin_home_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data="adm:add"))
    b.row(InlineKeyboardButton(text="✏️ Редактировать товар", callback_data="adm:edit"))
    return b.as_markup()


def _choice_kb(prefix: str, items: list[tuple[str, str]], back: str | None = None, width: int = 2) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for text, value in items:
        b.button(text=text, callback_data=f"{prefix}:{value}")
    b.adjust(width)
    if back:
        b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back))
    return b.as_markup()


def _sizes_select_kb(selected: set[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in SIZES:
        mark = "✅" if s in selected else "☑️"
        b.button(text=f"{mark} {s}", callback_data=f"add:size:{s}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="Готово", callback_data="add:size:done"))
    b.row(InlineKeyboardButton(text="Отмена", callback_data="add:cancel"))
    return b.as_markup()


def _photos_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="Готово", callback_data="add:photos:done"))
    b.row(InlineKeyboardButton(text="Пропустить", callback_data="add:photos:skip"))
    b.row(InlineKeyboardButton(text="Отмена", callback_data="add:cancel"))
    return b.as_markup()


def _product_actions_kb(token: str, is_active: bool, is_sale: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=("🔴 Снять с ленты" if is_active else "🟢 Включить в ленту"),
            callback_data=f"prd:active:{token}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=("🔥 Убрать распродажу" if is_sale else "🔥 Пометить распродажа"),
            callback_data=f"prd:sale:{token}",
        )
    )
    b.row(InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"prd:price:{token}"))
    b.row(InlineKeyboardButton(text="📏 Размеры", callback_data=f"prd:sizes:{token}"))
    b.row(InlineKeyboardButton(text="🎨 Цвета", callback_data=f"prd:colors:{token}"))
    return b.as_markup()


def _sizes_manage_kb(token: str, active_sizes: set[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in SIZES:
        mark = "✅" if s in active_sizes else "☑️"
        b.row(InlineKeyboardButton(text=f"{mark} {s}", callback_data=f"prd:sz:{token}:{s}"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prd:back:{token}"))
    return b.as_markup()


def _colors_manage_kb(token: str, colors: list[dict], color_tokens: dict[str, tuple[str, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in colors:
        ct = _new_token()
        color_tokens[ct] = (token, c["color"])
        mark = "✅" if c.get("is_active") else "☑️"
        b.row(InlineKeyboardButton(text=f"{mark} {c['color']}", callback_data=f"prd:cl:{ct}"))
    b.row(InlineKeyboardButton(text="➕ Добавить цвет", callback_data=f"prd:cladd:{token}"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prd:back:{token}"))
    return b.as_markup()


def _render_product_text(p: dict) -> str:
    out = []
    if p.get("is_sale"):
        out.append("🔥 РАСПРОДАЖА")
    out.append(f"Артикул: {p['sku']}")
    out.append(f"Название: {p.get('title', '')}")
    out.append(f"Цена: {p.get('price', 0)} {p.get('currency', 'RUB')}")
    out.append(f"Статус: {'АКТИВЕН' if p.get('is_active') else 'НЕ АКТИВЕН'}")
    out.append(f"Пол: {p.get('gender', '')}")
    out.append(f"Категория: {p.get('category', '')}")
    out.append(f"Сезон: {p.get('season', '')}")
    if p.get("insulation"):
        out.append(f"Утеплитель: {p.get('insulation')}")
    if p.get("material"):
        out.append(f"Материал: {p.get('material')}")
    sizes = [x["size"] for x in p.get("sizes", []) if x.get("is_active")]
    colors = [x["color"] for x in p.get("colors", []) if x.get("is_active")]
    out.append("Размеры: " + (", ".join(sizes) if sizes else "—"))
    out.append("Цвета: " + (", ".join(colors) if colors else "—"))
    desc = (p.get("description") or "").strip()
    if desc:
        out.append("")
        out.append(desc)
    return "\n".join(out)


@dataclass
class AddSession:
    step: str = "sku"
    sku: str = ""
    title: str = ""
    gender: str = ""
    category: str = ""
    season: str = ""
    insulation: str = ""
    material: str = ""
    colors: list[str] = field(default_factory=list)
    sizes: set[str] = field(default_factory=set)
    price: float = 0.0
    description: str = ""
    is_sale: bool = False
    photo_file_ids: list[str] = field(default_factory=list)


@dataclass
class PendingInput:
    mode: str
    sku: str


_ADD_SESSIONS: dict[int, AddSession] = {}
_PENDING: dict[int, PendingInput] = {}
_CARD_TOKENS: dict[str, str] = {}
_COLOR_TOKENS: dict[str, tuple[str, str]] = {}


def _parse_sku_cmd(text: str) -> tuple[str, str]:
    t = (text or "").strip()
    if not t:
        return "", ""
    parts = t.split(maxsplit=1)
    sku = parts[0].strip()
    cmd = parts[1].strip().lower() if len(parts) == 2 else ""
    return sku, cmd


async def _save_add_session(s: AddSession) -> None:
    await upsert_product(
        sku=s.sku,
        title=s.title,
        description=_ensure_note(s.description),
        gender=s.gender,
        category=s.category,
        season=s.season,
        insulation=s.insulation,
        material=s.material,
        price=s.price,
        currency="RUB",
        is_active=True,
        is_sale=s.is_sale,
    )

    for size in SIZES:
        await set_variant_active(s.sku, size, size in s.sizes)

    for color in s.colors:
        await add_color(s.sku, color)

    for file_id in s.photo_file_ids:
        await add_photo_file_id(s.sku, file_id)


async def send_product_card(chat_id: int, sku: str, bot: Bot) -> None:
    p = await get_product(sku)
    if not p:
        await bot.send_message(chat_id, f"Товар {sku} не найден в базе.")
        return

    token = _new_token()
    _CARD_TOKENS[token] = sku

    text = _render_product_text(p)
    kb = _product_actions_kb(token, bool(p["is_active"]), bool(p["is_sale"]))

    photos = p.get("photo_file_ids") or []
    if photos:
        await bot.send_photo(chat_id, photos[0], caption=text, reply_markup=kb)
        for fid in photos[1:3]:
            await bot.send_photo(chat_id, fid)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)


@router.message(Command("start"), PRIVATE_FILTER, ADMIN_FILTER)
async def admin_start(m: Message):
    if not m.from_user or m.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(m.from_user.id):
        return
    await m.answer("Админ-меню:", reply_markup=_admin_home_kb())


@router.callback_query(F.data == "adm:edit")
async def adm_edit(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.answer()
    if cb.message:
        await cb.message.answer("Пришли артикул товара, и я покажу карточку.")


@router.callback_query(F.data == "adm:add")
async def adm_add_start(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _ADD_SESSIONS[cb.from_user.id] = AddSession(step="sku")
    await cb.answer()
    if cb.message:
        await cb.message.answer(
            "Добавление товара.\n\nШаг 1/10: пришли артикул товара.\n"
            "Артикул обязателен и должен быть уникальным."
        )


@router.callback_query(F.data == "add:cancel")
async def add_cancel(cb: CallbackQuery):
    if not cb.from_user:
        return
    _ADD_SESSIONS.pop(cb.from_user.id, None)
    await cb.answer("Отменено")
    if cb.message:
        await cb.message.answer("Добавление товара отменено.")


@router.callback_query(F.data.startswith("add:gender:"))
async def add_gender(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "gender":
        return await cb.answer()

    s.gender = (cb.data or "").split(":", 2)[2]
    s.step = "category"
    await cb.answer()
    if cb.message:
        await cb.message.edit_text(
            "Шаг 3/10: выбери категорию.",
            reply_markup=_choice_kb("add:category", CATEGORIES, back="add:cancel", width=2),
        )


@router.callback_query(F.data.startswith("add:category:"))
async def add_category(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "category":
        return await cb.answer()

    s.category = (cb.data or "").split(":", 2)[2]
    s.step = "season"
    await cb.answer()
    if cb.message:
        await cb.message.edit_text(
            "Шаг 4/10: выбери сезон.",
            reply_markup=_choice_kb("add:season", SEASONS, back="add:cancel", width=2),
        )


@router.callback_query(F.data.startswith("add:season:"))
async def add_season(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "season":
        return await cb.answer()

    s.season = (cb.data or "").split(":", 2)[2]
    s.step = "insulation"
    await cb.answer()
    if cb.message:
        await cb.message.edit_text(
            "Шаг 5/10: выбери утеплитель.",
            reply_markup=_choice_kb("add:ins", INSULATIONS, back="add:cancel", width=2),
        )


@router.callback_query(F.data.startswith("add:ins:"))
async def add_insulation(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "insulation":
        return await cb.answer()

    s.insulation = (cb.data or "").split(":", 2)[2]
    s.step = "material"
    await cb.answer()
    if cb.message:
        await cb.message.edit_text("Шаг 6/10: напиши материал товара текстом.")


@router.callback_query(F.data.startswith("add:size:"))
async def add_size_toggle(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "sizes":
        return await cb.answer()

    value = (cb.data or "").split(":", 2)[2]
    if value == "done":
        if not s.sizes:
            return await cb.answer("Выбери хотя бы один размер", show_alert=True)
        s.step = "price"
        await cb.answer()
        if cb.message:
            await cb.message.edit_text("Шаг 9/10: введи цену одним числом, например 5990.")
        return

    if value in s.sizes:
        s.sizes.remove(value)
    else:
        s.sizes.add(value)

    await cb.answer("Ок")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=_sizes_select_kb(s.sizes))


@router.callback_query(F.data.startswith("add:sale:"))
async def add_sale(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "sale":
        return await cb.answer()

    s.is_sale = ((cb.data or "").split(":", 2)[2] == "1")
    s.step = "photos"
    await cb.answer()
    if cb.message:
        await cb.message.edit_text(
            "Шаг 10/10: отправь 1 или несколько фото товара.\n"
            "Когда закончишь — нажми «Готово».\n"
            "Если фото пока нет — нажми «Пропустить».",
            reply_markup=_photos_kb(),
        )


@router.callback_query(F.data == "add:photos:done")
async def add_photos_done(cb: CallbackQuery, bot: Bot):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "photos":
        return await cb.answer()

    await _save_add_session(s)
    sku = s.sku
    _ADD_SESSIONS.pop(cb.from_user.id, None)

    await cb.answer("Сохранено")
    if cb.message:
        await cb.message.answer(f"Товар {sku} сохранён.")
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data == "add:photos:skip")
async def add_photos_skip(cb: CallbackQuery, bot: Bot):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "photos":
        return await cb.answer()

    await _save_add_session(s)
    sku = s.sku
    _ADD_SESSIONS.pop(cb.from_user.id, None)

    await cb.answer("Сохранено")
    if cb.message:
        await cb.message.answer(f"Товар {sku} сохранён без фото.")
    await send_product_card(cb.from_user.id, sku, bot)


@router.message(F.photo, PRIVATE_FILTER, ADMIN_FILTER)
async def admin_photo_input(m: Message):
    if not m.from_user or m.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(m.from_user.id):
        return

    s = _ADD_SESSIONS.get(m.from_user.id)
    if not s or s.step != "photos":
        return

    photo = m.photo[-1]
    s.photo_file_ids.append(photo.file_id)
    await m.answer(
        f"Фото добавлено ({len(s.photo_file_ids)}). Можешь отправить ещё или нажать «Готово».",
        reply_markup=_photos_kb(),
    )


@router.message(F.text, PRIVATE_FILTER, ADMIN_FILTER)
async def admin_text_router(m: Message, bot: Bot):
    if not m.from_user or m.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(m.from_user.id):
        return

    user_id = m.from_user.id
    text = (m.text or "").strip()

    # pending edit actions
    pend = _PENDING.get(user_id)
    if pend:
        if pend.mode == "price":
            try:
                price = float(text.replace(",", "."))
            except ValueError:
                return await m.answer("Цена должна быть числом. Например: 5990")
            await update_product_price(pend.sku, price)
            _PENDING.pop(user_id, None)
            await m.answer("Цена обновлена.")
            await send_product_card(m.chat.id, pend.sku, bot)
            return

        if pend.mode == "add_color":
            color = text.strip()
            if not color:
                return await m.answer("Напиши цвет текстом.")
            await add_color(pend.sku, color)
            _PENDING.pop(user_id, None)
            await m.answer("Цвет добавлен.")
            await send_product_card(m.chat.id, pend.sku, bot)
            return

    # add flow
    s = _ADD_SESSIONS.get(user_id)
    if s:
        if s.step == "sku":
            sku = _normalize_sku(text)
            if not sku:
                return await m.answer("Артикул не должен быть пустым.")
            exists = await get_product(sku)
            if exists:
                await m.answer(
                    "Товар с таким артикулом уже есть в базе. "
                    "Пришли другой артикул или просто напиши существующий артикул для редактирования."
                )
                return
            s.sku = sku
            s.step = "title"
            await m.answer("Шаг 2/10: напиши название товара.")
            return

        if s.step == "title":
            s.title = text
            s.step = "gender"
            await m.answer(
                "Выбери пол:",
                reply_markup=_choice_kb("add:gender", GENDERS, back="add:cancel", width=2),
            )
            return

        if s.step == "material":
            s.material = text
            s.step = "colors"
            await m.answer("Шаг 7/10: напиши цвета через запятую. Например: черный, серый, белый")
            return

        if s.step == "colors":
            colors = [x.strip() for x in text.split(",") if x.strip()]
            if not colors:
                return await m.answer("Нужно указать хотя бы один цвет.")
            s.colors = colors
            s.step = "sizes"
            await m.answer(
                "Шаг 8/10: выбери размеры.",
                reply_markup=_sizes_select_kb(s.sizes),
            )
            return

        if s.step == "price":
            try:
                s.price = float(text.replace(",", "."))
            except ValueError:
                return await m.answer("Цена должна быть числом. Например: 5990")
            s.step = "description"
            await m.answer("Шаг 9/10: напиши описание товара. Если описания нет — отправь '-'")
            return

        if s.step == "description":
            s.description = "" if text == "-" else text
            s.step = "sale"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Да", callback_data="add:sale:1")],
                    [InlineKeyboardButton(text="Нет", callback_data="add:sale:0")],
                    [InlineKeyboardButton(text="Отмена", callback_data="add:cancel")],
                ]
            )
            await m.answer("Пометить товар как распродажа?", reply_markup=kb)
            return

        if s.step == "photos":
            await m.answer("Сейчас жду фото. Отправь фото или нажми кнопку «Готово/Пропустить».", reply_markup=_photos_kb())
            return

    # sku shortcuts
    sku, cmd = _parse_sku_cmd(text)
    if sku:
        product = await get_product(sku)
        if product:
            if "продан" in cmd:
                await set_product_active(sku, False)
                await m.answer(f"Ок, {sku} снят с ленты.")
                await send_product_card(m.chat.id, sku, bot)
                return

            if "актив" in cmd or "включ" in cmd:
                await set_product_active(sku, True)
                await m.answer(f"Ок, {sku} снова активен.")
                await send_product_card(m.chat.id, sku, bot)
                return

            await send_product_card(m.chat.id, sku, bot)
            return

    # если текст админа не подошёл ни под один админ-сценарий — просто не мешаем user router


@router.callback_query(F.data.startswith("prd:active:"))
async def cb_toggle_active(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    p = await get_product(sku)
    if not p:
        return await cb.answer("Товар не найден", show_alert=True)

    await set_product_active(sku, not bool(p.get("is_active")))
    await cb.answer("Готово")
    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data.startswith("prd:sale:"))
async def cb_toggle_sale(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    await toggle_product_sale(sku)
    await cb.answer("Готово")
    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data.startswith("prd:price:"))
async def cb_price(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    _PENDING[cb.from_user.id] = PendingInput(mode="price", sku=sku)
    await cb.answer()
    if cb.message:
        await cb.message.answer(f"Введи новую цену для {sku} одним числом.")


@router.callback_query(F.data.startswith("prd:sizes:"))
async def cb_sizes(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)

    active_sizes = {x["size"] for x in p.get("sizes", []) if x.get("is_active")}
    await cb.answer()
    if cb.message:
        await cb.message.edit_text(
            f"Размеры для {sku}:",
            reply_markup=_sizes_manage_kb(token, active_sizes),
        )


@router.callback_query(F.data.startswith("prd:sz:"))
async def cb_size_toggle(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    _, _, token, size = (cb.data or "").split(":", 3)
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)

    active_sizes = {x["size"] for x in p.get("sizes", []) if x.get("is_active")}
    await set_variant_active(sku, size, size not in active_sizes)

    p2 = await get_product(sku)
    active_sizes2 = {x["size"] for x in p2.get("sizes", []) if x.get("is_active")}
    await cb.answer("Ок")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=_sizes_manage_kb(token, active_sizes2))


@router.callback_query(F.data.startswith("prd:colors:"))
async def cb_colors(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)

    await cb.answer()
    if cb.message:
        await cb.message.edit_text(
            f"Цвета для {sku}:",
            reply_markup=_colors_manage_kb(token, p.get("colors", []), _COLOR_TOKENS),
        )


@router.callback_query(F.data.startswith("prd:cladd:"))
async def cb_color_add(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    _PENDING[cb.from_user.id] = PendingInput(mode="add_color", sku=sku)
    await cb.answer()
    if cb.message:
        await cb.message.answer(f"Напиши новый цвет для {sku} текстом.")


@router.callback_query(F.data.startswith("prd:cl:"))
async def cb_color_toggle(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    ct = (cb.data or "").split(":")[2]
    pair = _COLOR_TOKENS.get(ct)
    if not pair:
        return await cb.answer("Кнопка устарела. Открой товар снова.", show_alert=True)

    token, color = pair
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    p = await get_product(sku)
    if not p:
        return await cb.answer("Не найдено", show_alert=True)

    colors = {x["color"]: bool(x["is_active"]) for x in p.get("colors", [])}
    await set_color_active(sku, color, not colors.get(color, True))

    p2 = await get_product(sku)
    await cb.answer("Ок")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=_colors_manage_kb(token, p2.get("colors", []), _COLOR_TOKENS))


@router.callback_query(F.data.startswith("prd:back:"))
async def cb_back(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        return await cb.answer("Карточка устарела. Открой товар снова.", show_alert=True)

    await cb.answer()
    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)

