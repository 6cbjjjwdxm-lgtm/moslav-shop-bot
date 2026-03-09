from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from .db import (
    add_color,
    add_photo_file_id,
    clear_product_publications,
    delete_product,
    get_product,
    get_product_publications,
    save_product_publication,
    set_color_active,
    set_product_active,
    set_variant_active,
    toggle_product_sale,
    update_product_description,
    update_product_price,
    upsert_product,
)

router = Router(name="admin")

MY_ADMIN_ID = 459980503
ALL_ADMIN_IDS = list(set([MY_ADMIN_ID, *list(getattr(settings, "admin_id_set", set()))]))

ADMIN_FILTER = F.from_user.id.in_(ALL_ADMIN_IDS)
PRIVATE_FILTER = F.chat.type == ChatType.PRIVATE

WHOLESALE_NOTE = "По вопросам закупок по оптовым ценам обращайтесь по тел. +7(903)776-17-47"

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
    ("Анорак", "anorak"),
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

GENDER_LABELS = {
    "male": "мужской",
    "female": "женский",
}

CATEGORY_LABELS = {
    "pants": "брюки",
    "hoodie": "толстовка",
    "tshirt": "футболка",
    "shorts": "шорты",
    "vest": "безрукавка",
    "anorak": "анорак",
    "jacket": "куртка",
    "tracksuit": "спортивный костюм",
}

SEASON_LABELS = {
    "summer": "лето",
    "autumn": "осень",
    "winter": "зима",
    "euro_winter": "еврозима",
}

INSULATION_LABELS = {
    "": "",
    "thinsulate": "тинсулейт",
    "holofiber": "холофайбер",
    "down": "пух",
}


def _is_admin(user_id: int) -> bool:
    return user_id in ALL_ADMIN_IDS


def _new_token() -> str:
    return secrets.token_hex(4)


def _normalize_sku(text: str) -> str:
    return (text or "").strip()


def _ensure_note(description: str) -> str:
    return (description or "").strip()


def _format_price(value: float | int | str) -> str:
    try:
        raw = str(value).replace(" ", "").replace(",", ".")
        f = float(raw)
        if f.is_integer():
            return f"{int(f):,}".replace(",", " ")
        return f"{f:,.2f}".replace(",", " ").replace(".00", "")
    except Exception:
        return str(value).strip()


def _unique_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        val = (item or "").strip()
        if not val:
            continue
        if val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def _sort_sizes(items: list[str]) -> list[str]:
    order = {size: i for i, size in enumerate(SIZES)}
    cleaned = _unique_keep_order(items)
    return sorted(cleaned, key=lambda x: order.get(x, 999))


async def _safe_answer_callback(
    cb: CallbackQuery,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    try:
        await cb.answer(text or "", show_alert=show_alert)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "query is too old" in msg or "query id is invalid" in msg:
            return
        raise


def _admin_home_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data="adm:add"))
    b.row(InlineKeyboardButton(text="✏️ Редактировать товар", callback_data="adm:edit"))
    return b.as_markup()


def _choice_kb(
    prefix: str,
    items: list[tuple[str, str]],
    back: str | None = None,
    width: int = 2,
) -> InlineKeyboardMarkup:
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
    b.row(InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"prd:desc:{token}"))
    b.row(InlineKeyboardButton(text="📏 Размеры", callback_data=f"prd:sizes:{token}"))
    b.row(InlineKeyboardButton(text="🎨 Цвета", callback_data=f"prd:colors:{token}"))
    b.row(InlineKeyboardButton(text="📢 Опубликовать в канал", callback_data=f"prd:pub:{token}"))
    b.row(InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"prd:del:{token}"))
    return b.as_markup()


def _sizes_manage_kb(token: str, active_sizes: set[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in SIZES:
        mark = "✅" if s in active_sizes else "☑️"
        b.row(InlineKeyboardButton(text=f"{mark} {s}", callback_data=f"prd:sz:{token}:{s}"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prd:back:{token}"))
    return b.as_markup()


def _colors_manage_kb(
    token: str,
    colors: list[dict],
    color_tokens: dict[str, tuple[str, str]],
) -> InlineKeyboardMarkup:
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
    sizes = _sort_sizes([x["size"] for x in p.get("sizes", []) if x.get("is_active") and x.get("size")])
    colors = _unique_keep_order([x["color"] for x in p.get("colors", []) if x.get("is_active") and x.get("color")])

    gender = GENDER_LABELS.get(p.get("gender", ""), p.get("gender", ""))
    category = CATEGORY_LABELS.get(p.get("category", ""), p.get("category", ""))
    season = SEASON_LABELS.get(p.get("season", ""), p.get("season", ""))
    insulation = INSULATION_LABELS.get(p.get("insulation", ""), p.get("insulation", ""))

    currency_raw = (p.get("currency") or "RUB").strip().upper()
    currency = "₽" if currency_raw == "RUB" else currency_raw

    out = []

    if p.get("is_sale"):
        out.append("🔥 РАСПРОДАЖА")

    title = (p.get("title") or "").strip()
    if title:
        out.append(f"🛍 {title}")

    out.append(f"🏷️ Артикул: {p['sku']}")
    out.append(f"💰 Цена: {_format_price(p.get('price', 0))} {currency}")
    out.append(f"Статус: {'АКТИВЕН' if p.get('is_active') else 'НЕ АКТИВЕН'}")

    if gender:
        out.append(f"👤 Пол: {gender}")
    if category:
        out.append(f"🧥 Категория: {category}")
    if season:
        out.append(f"🌦️ Сезон: {season}")
    if insulation:
        out.append(f"❄️ Утеплитель: {insulation}")
    if p.get("material"):
        out.append(f"🧵 Материал: {p['material']}")

    out.append("📏 Размеры: " + (", ".join(sizes) if sizes else "—"))
    out.append("🎨 Цвета: " + (", ".join(colors) if colors else "—"))

    desc = (p.get("description") or "").strip()
    if desc:
        out.append("")
        out.append("📝 Описание:")
        out.append(desc)

    return "\n".join(out)


def _render_channel_text(p: dict) -> str:
    sizes = _sort_sizes([x["size"] for x in p.get("sizes", []) if x.get("is_active") and x.get("size")])
    colors = _unique_keep_order([x["color"] for x in p.get("colors", []) if x.get("is_active") and x.get("color")])

    gender = GENDER_LABELS.get(p.get("gender", ""), p.get("gender", ""))
    category = CATEGORY_LABELS.get(p.get("category", ""), p.get("category", ""))
    season = SEASON_LABELS.get(p.get("season", ""), p.get("season", ""))
    insulation = INSULATION_LABELS.get(p.get("insulation", ""), p.get("insulation", ""))

    currency_raw = (p.get("currency") or "RUB").strip().upper()
    currency = "₽" if currency_raw == "RUB" else currency_raw

    title = (p.get("title") or "").strip()
    if not title:
        fallback_parts = []
        if gender:
            fallback_parts.append(gender.capitalize())
        if category:
            fallback_parts.append(category)
        title = " ".join(fallback_parts).strip()

    lines = []

    if p.get("is_sale"):
        lines.append("🔥 РАСПРОДАЖА")

    if title:
        lines.append(f"🛍 {title}")

    if lines:
        lines.append("")

    if p.get("price") not in (None, ""):
        lines.append(f"💰 {_format_price(p.get('price', 0))} {currency}")

    sku = str(p.get("sku") or "").strip()
    if sku:
        lines.append(f"🏷️ Артикул: {sku}")

    info_lines = []

    if season:
        info_lines.append(f"🌦️ Сезон: {season}")
    if insulation:
        info_lines.append(f"❄️ Утеплитель: {insulation}")
    if p.get("material"):
        info_lines.append(f"🧵 Материал: {p['material']}")
    if sizes:
        info_lines.append("📏 Размеры: " + ", ".join(sizes))
    if colors:
        info_lines.append("🎨 Цвета: " + ", ".join(colors))

    if info_lines:
        lines.append("")
        lines.extend(info_lines)

    desc = (p.get("description") or "").strip()
    if desc:
        lines.append("")
        lines.append("📝 Описание:")
        lines.append(desc)

    lines.append("")
    lines.append(f"📞 {WHOLESALE_NOTE}")

    return "\n".join(lines)


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

    for color in _unique_keep_order(s.colors):
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


async def remove_product_from_channel(bot: Bot, sku: str) -> None:
    channel_id = getattr(settings, "CHANNEL_ID", "").strip()
    if not channel_id:
        return

    publications = await get_product_publications(sku)
    if not publications:
        return

    for pub in publications:
        message_id = pub.get("message_id")
        if not message_id:
            continue
        try:
            await bot.delete_message(chat_id=channel_id, message_id=message_id)
        except Exception:
            pass

    await clear_product_publications(sku)


async def publish_product_to_channel(bot: Bot, sku: str) -> None:
    p = await get_product(sku)
    if not p:
        raise RuntimeError("Товар не найден.")

    channel_id = getattr(settings, "CHANNEL_ID", "").strip()
    if not channel_id:
        raise RuntimeError("Не задан CHANNEL_ID.")

    text = _render_channel_text(p)
    photos = p.get("photo_file_ids") or []

    await remove_product_from_channel(bot, sku)

    if len(photos) >= 2:
        media = []
        for i, fid in enumerate(photos[:10]):
            if i == 0:
                media.append(InputMediaPhoto(media=fid, caption=text))
            else:
                media.append(InputMediaPhoto(media=fid))

        messages = await bot.send_media_group(
            chat_id=channel_id,
            media=media,
        )

        for msg in messages:
            await save_product_publication(
                sku=sku,
                chat_id=str(channel_id),
                message_id=msg.message_id,
            )
        return

    if len(photos) == 1:
        msg = await bot.send_photo(
            chat_id=channel_id,
            photo=photos[0],
            caption=text,
        )
        await save_product_publication(
            sku=sku,
            chat_id=str(channel_id),
            message_id=msg.message_id,
        )
        return

    msg = await bot.send_message(
        chat_id=channel_id,
        text=text,
    )
    await save_product_publication(
        sku=sku,
        chat_id=str(channel_id),
        message_id=msg.message_id,
    )


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
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)
    if cb.message:
        await cb.message.answer("Пришли артикул товара, и я покажу карточку.")


@router.callback_query(F.data == "adm:add")
async def adm_add_start(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    _ADD_SESSIONS[cb.from_user.id] = AddSession(step="sku")
    await _safe_answer_callback(cb)
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
    await _safe_answer_callback(cb, "Отменено")
    if cb.message:
        await cb.message.answer("Добавление товара отменено.")


@router.callback_query(F.data.startswith("add:gender:"))
async def add_gender(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "gender":
        return await _safe_answer_callback(cb)

    s.gender = (cb.data or "").split(":", 2)[2]
    s.step = "category"
    await _safe_answer_callback(cb)
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
        return await _safe_answer_callback(cb)

    s.category = (cb.data or "").split(":", 2)[2]
    s.step = "season"
    await _safe_answer_callback(cb)
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
        return await _safe_answer_callback(cb)

    s.season = (cb.data or "").split(":", 2)[2]
    s.step = "insulation"
    await _safe_answer_callback(cb)
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
        return await _safe_answer_callback(cb)

    s.insulation = (cb.data or "").split(":", 2)[2]
    s.step = "material"
    await _safe_answer_callback(cb)
    if cb.message:
        await cb.message.edit_text("Шаг 6/10: напиши материал товара текстом.")


@router.callback_query(F.data.startswith("add:size:"))
async def add_size_toggle(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "sizes":
        return await _safe_answer_callback(cb)

    value = (cb.data or "").split(":", 2)[2]
    if value == "done":
        if not s.sizes:
            return await _safe_answer_callback(cb, "Выбери хотя бы один размер", show_alert=True)
        s.step = "price"
        await _safe_answer_callback(cb)
        if cb.message:
            await cb.message.edit_text("Шаг 9/10: введи цену одним числом, например 5990.")
        return

    if value in s.sizes:
        s.sizes.remove(value)
    else:
        s.sizes.add(value)

    await _safe_answer_callback(cb, "Ок")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=_sizes_select_kb(s.sizes))


@router.callback_query(F.data.startswith("add:sale:"))
async def add_sale(cb: CallbackQuery):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "sale":
        return await _safe_answer_callback(cb)

    s.is_sale = ((cb.data or "").split(":", 2)[2] == "1")
    s.step = "photos"
    await _safe_answer_callback(cb)
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
        return await _safe_answer_callback(cb)

    await _safe_answer_callback(cb, "Сохраняю...")
    await _save_add_session(s)
    sku = s.sku
    _ADD_SESSIONS.pop(cb.from_user.id, None)

    if cb.message:
        await cb.message.answer(f"Товар {sku} сохранён.")
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data == "add:photos:skip")
async def add_photos_skip(cb: CallbackQuery, bot: Bot):
    if not cb.from_user:
        return
    s = _ADD_SESSIONS.get(cb.from_user.id)
    if not s or s.step != "photos":
        return await _safe_answer_callback(cb)

    await _safe_answer_callback(cb, "Сохраняю...")
    await _save_add_session(s)
    sku = s.sku
    _ADD_SESSIONS.pop(cb.from_user.id, None)

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

        if pend.mode == "description":
            description = "" if text == "-" else text
            await update_product_description(pend.sku, description)
            _PENDING.pop(user_id, None)
            await m.answer("Описание обновлено.")
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
            await m.answer(
                "Сейчас жду фото. Отправь фото или нажми кнопку «Готово/Пропустить».",
                reply_markup=_photos_kb(),
            )
            return

    sku, cmd = _parse_sku_cmd(text)
    if sku:
        product = await get_product(sku)
        if product:
            if "продан" in cmd:
                await set_product_active(sku, False)
                await remove_product_from_channel(bot, sku)
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


@router.callback_query(F.data.startswith("prd:active:"))
async def cb_toggle_active(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb, "Готово")

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    p = await get_product(sku)
    if not p:
        if cb.message:
            await cb.message.answer("Товар не найден.")
        return

    new_active = not bool(p.get("is_active"))
    await set_product_active(sku, new_active)

    if not new_active:
        await remove_product_from_channel(bot, sku)

    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data.startswith("prd:sale:"))
async def cb_toggle_sale(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb, "Готово")

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    await toggle_product_sale(sku)

    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)


@router.callback_query(F.data.startswith("prd:price:"))
async def cb_price(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    _PENDING[cb.from_user.id] = PendingInput(mode="price", sku=sku)
    if cb.message:
        await cb.message.answer(f"Введи новую цену для {sku} одним числом.")


@router.callback_query(F.data.startswith("prd:desc:"))
async def cb_description(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    _PENDING[cb.from_user.id] = PendingInput(mode="description", sku=sku)
    if cb.message:
        await cb.message.answer(
            f"Введи новое описание для {sku}.\n"
            f"Если хочешь очистить описание — отправь -"
        )


@router.callback_query(F.data.startswith("prd:sizes:"))
async def cb_sizes(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    p = await get_product(sku)
    if not p:
        if cb.message:
            await cb.message.answer("Не найдено.")
        return

    active_sizes = {x["size"] for x in p.get("sizes", []) if x.get("is_active")}
    if cb.message:
        await cb.message.edit_text(
            f"Размеры для {sku}:",
            reply_markup=_sizes_manage_kb(token, active_sizes),
        )


@router.callback_query(F.data.startswith("prd:sz:"))
async def cb_size_toggle(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb, "Ок")

    _, _, token, size = (cb.data or "").split(":", 3)
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    p = await get_product(sku)
    if not p:
        if cb.message:
            await cb.message.answer("Не найдено.")
        return

    active_sizes = {x["size"] for x in p.get("sizes", []) if x.get("is_active")}
    await set_variant_active(sku, size, size not in active_sizes)

    p2 = await get_product(sku)
    active_sizes2 = {x["size"] for x in p2.get("sizes", []) if x.get("is_active")}
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=_sizes_manage_kb(token, active_sizes2))


@router.callback_query(F.data.startswith("prd:colors:"))
async def cb_colors(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    p = await get_product(sku)
    if not p:
        if cb.message:
            await cb.message.answer("Не найдено.")
        return

    if cb.message:
        await cb.message.edit_text(
            f"Цвета для {sku}:",
            reply_markup=_colors_manage_kb(token, p.get("colors", []), _COLOR_TOKENS),
        )


@router.callback_query(F.data.startswith("prd:cladd:"))
async def cb_color_add(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    _PENDING[cb.from_user.id] = PendingInput(mode="add_color", sku=sku)
    if cb.message:
        await cb.message.answer(f"Напиши новый цвет для {sku} текстом.")


@router.callback_query(F.data.startswith("prd:cl:"))
async def cb_color_toggle(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb, "Ок")

    ct = (cb.data or "").split(":")[2]
    pair = _COLOR_TOKENS.get(ct)
    if not pair:
        if cb.message:
            await cb.message.answer("Кнопка устарела. Открой товар снова.")
        return

    token, color = pair
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    p = await get_product(sku)
    if not p:
        if cb.message:
            await cb.message.answer("Не найдено.")
        return

    colors = {x["color"]: bool(x["is_active"]) for x in p.get("colors", [])}
    await set_color_active(sku, color, not colors.get(color, True))

    p2 = await get_product(sku)
    if cb.message:
        await cb.message.edit_reply_markup(
            reply_markup=_colors_manage_kb(token, p2.get("colors", []), _COLOR_TOKENS)
        )


@router.callback_query(F.data.startswith("prd:pub:"))
async def cb_publish(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb, "Публикую...")

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    try:
        await publish_product_to_channel(bot, sku)
    except Exception as e:
        if cb.message:
            await cb.message.answer(f"Ошибка публикации: {e}")
        return

    if cb.message:
        await cb.message.answer("Товар опубликован в канал.")


@router.callback_query(F.data.startswith("prd:del:"))
async def cb_delete_product_ask(cb: CallbackQuery):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"prd:delok:{token}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"prd:back:{token}")],
        ]
    )

    if cb.message:
        await cb.message.answer(f"Удалить товар {sku} из базы данных?", reply_markup=kb)


@router.callback_query(F.data.startswith("prd:delok:"))
async def cb_delete_product_confirm(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb, "Удаляю...")

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    await remove_product_from_channel(bot, sku)
    await delete_product(sku)

    for k, v in list(_CARD_TOKENS.items()):
        if v == sku:
            _CARD_TOKENS.pop(k, None)

    if cb.message:
        await cb.message.answer(f"Товар {sku} полностью удалён из базы.")


@router.callback_query(F.data.startswith("prd:back:"))
async def cb_back(cb: CallbackQuery, bot: Bot):
    if not cb.from_user or not _is_admin(cb.from_user.id):
        return await _safe_answer_callback(cb, "Нет доступа", show_alert=True)

    await _safe_answer_callback(cb)

    token = (cb.data or "").split(":")[2]
    sku = _CARD_TOKENS.get(token)
    if not sku:
        if cb.message:
            await cb.message.answer("Карточка устарела. Открой товар снова.")
        return

    if cb.message:
        await cb.message.delete()
    await send_product_card(cb.from_user.id, sku, bot)




