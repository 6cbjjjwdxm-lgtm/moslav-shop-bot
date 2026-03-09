from __future__ import annotations

import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import settings
from .db import (
    clear_sales_session,
    create_sales_order,
    get_product,
    get_sales_order_by_no,
    get_sales_session,
    set_sales_order_tracking,
    update_sales_order_stage,
    upsert_sales_session,
)

router = Router(name="sales")

MY_ADMIN_ID = 459980503
ALL_ADMIN_IDS = list(set([MY_ADMIN_ID, *list(getattr(settings, "admin_id_set", set()))]))

PAYMENT_URL_TEMPLATE = "https://example.com/pay/{order_no}"


def _is_admin(user_id: int) -> bool:
    return user_id in ALL_ADMIN_IDS


def _format_price(value: float | int | str) -> str:
    try:
        raw = str(value).replace(" ", "").replace(",", ".")
        f = float(raw)
        if f.is_integer():
            return f"{int(f):,}".replace(",", " ")
        return f"{f:,.2f}".replace(",", " ").replace(".00", "")
    except Exception:
        return str(value).strip()


def _parse_start_param(text: str) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _detect_psychotype(text: str) -> tuple[str, float]:
    t = (text or "").lower()

    rational_words = ["цена", "состав", "материал", "размер", "доставка", "сколько", "качество"]
    cautious_words = ["гарантия", "возврат", "точно", "если", "вдруг", "переживаю", "надежно"]
    emotional_words = ["красиво", "стильно", "нравится", "вау", "хочу", "люблю"]
    decisive_words = ["беру", "оформляем", "оплатить", "куплю", "сейчас", "срочно"]

    scores = {
        "rational": sum(1 for w in rational_words if w in t),
        "cautious": sum(1 for w in cautious_words if w in t),
        "emotional": sum(1 for w in emotional_words if w in t),
        "decisive": sum(1 for w in decisive_words if w in t),
    }

    psychotype = max(scores, key=scores.get)
    score = scores[psychotype]
    conf = 0.0 if score == 0 else min(0.95, 0.35 + score * 0.15)
    return psychotype if score > 0 else "", conf


def _buyer_ready_to_checkout(text: str) -> bool:
    t = (text or "").lower()
    triggers = [
        "беру", "оформ", "хочу купить", "покупаю", "заказываю",
        "куда платить", "как оплатить", "готов оплатить", "оплатить"
    ]
    return any(x in t for x in triggers)


def _manager_style_intro(psychotype: str) -> str:
    if psychotype == "rational":
        return "Понял вас. Коротко и по делу:"
    if psychotype == "cautious":
        return "Понимаю ваш вопрос. Давайте спокойно уточним детали:"
    if psychotype == "emotional":
        return "Отличный выбор — модель действительно цепляет."
    if psychotype == "decisive":
        return "Отлично, идем быстро и по шагам."
    return "С радостью помогу."

def _product_preview_text(p: dict) -> str:
    title = (p.get("title") or "").strip() or p.get("sku", "")
    price = _format_price(p.get("price", 0))
    currency = "₽" if (p.get("currency") or "RUB").upper() == "RUB" else (p.get("currency") or "")
    return (
        f"🛍 {title}\n"
        f"🏷️ Артикул: {p.get('sku', '')}\n"
        f"💰 Цена: {price} {currency}\n\n"
        f"Расскажите, что для вас сейчас важнее: размер, посадка, сезон, материал или доставка?"
    )


def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Оформить заказ", callback_data="sale:checkout")],
        ]
    )


async def _send_product_preview(m: Message, sku: str) -> None:
    p = await get_product(sku)
    if not p:
        await m.answer(f"Открыт товар {sku}. Напишите, что хотите уточнить по нему.")
        return

    photos = p.get("photo_file_ids") or []
    text = _product_preview_text(p)

    if photos:
        await m.answer_photo(photo=photos[0], caption=text, reply_markup=_buy_kb())
    else:
        await m.answer(text, reply_markup=_buy_kb())


async def _notify_admins(bot, text: str) -> None:
    for admin_id in ALL_ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


@router.message(Command("start"))
async def sales_start(m: Message):
    if not m.from_user:
        return

    start_param = _parse_start_param(m.text or "")
    if start_param.startswith("manager_"):
        sku = start_param.removeprefix("manager_").strip()

        await upsert_sales_session(
            user_id=m.from_user.id,
            sku=sku,
            stage="profiling",
            psychotype="",
            psychotype_conf=0,
            context={},
        )

        await m.answer("Здравствуйте! Я менеджер магазина и помогу подобрать и оформить заказ.")
        await _send_product_preview(m, sku)
        return


@router.callback_query(F.data == "sale:checkout")
async def sale_checkout(cb):
    if not cb.from_user:
        return

    s = await get_sales_session(cb.from_user.id)
    if not s:
        await cb.answer()
        if cb.message:
            await cb.message.answer("Сессия устарела. Откройте товар заново из карточки.")
        return

    context = s.get("context", {})
    await upsert_sales_session(
        user_id=cb.from_user.id,
        sku=s.get("sku", ""),
        stage="collect_size",
        psychotype=s.get("psychotype", ""),
        psychotype_conf=s.get("psychotype_conf", 0),
        context=context,
    )

    await cb.answer()
    if cb.message:
        await cb.message.answer("Отлично. Напишите, пожалуйста, нужный размер.")


@router.message(Command("payok"))
async def admin_payok(m: Message):
    if not m.from_user or not _is_admin(m.from_user.id):
        return

    parts = (m.text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        return await m.answer("Формат: /payok MS-20260309-AB12")

    order_no = parts[1].strip()
    order = await get_sales_order_by_no(order_no)
    if not order:
        return await m.answer("Заказ не найден.")

    await update_sales_order_stage(order_no, "packing")
    await upsert_sales_session(
        user_id=order["user_id"],
        sku=order["sku"],
        stage="packing",
        psychotype=order.get("psychotype", ""),
        psychotype_conf=0,
        context={},
    )

    await m.bot.send_message(
        order["user_id"],
        "Оплата прошла успешно. Спасибо за заказ!\n\n"
        "Ваш заказ уже принят в работу и сейчас находится в процессе сборки "
        "и подготовки к отправке на ПВЗ.\n\n"
        "После передачи заказа в службу доставки ему будет присвоен трек-номер, "
        "и мы сразу отправим его вам в этот чат.\n\n"
        "Обычно подготовка и передача в доставку занимает 1–3 дня."
    )

    await m.answer(f"Заказ {order_no} переведен в packing, клиент уведомлен.")


@router.message(Command("track"))
async def admin_track(m: Message):
    if not m.from_user or not _is_admin(m.from_user.id):
        return

    parts = (m.text or "").strip().split(maxsplit=3)
    if len(parts) != 4:
        return await m.answer("Формат: /track MS-20260309-AB12 CDEK 1234567890")

    _, order_no, carrier, tracking_number = parts
    order = await get_sales_order_by_no(order_no)
    if not order:
        return await m.answer("Заказ не найден.")

    await set_sales_order_tracking(order_no, carrier, tracking_number)
    await upsert_sales_session(
        user_id=order["user_id"],
        sku=order["sku"],
        stage="shipped",
        psychotype=order.get("psychotype", ""),
        psychotype_conf=0,
        context={},
    )

    await m.bot.send_message(
        order["user_id"],
        f"Ваш заказ отправлен ✅\n\n"
        f"Номер заказа: {order_no}\n"
        f"Служба доставки: {carrier}\n"
        f"Трек-номер: {tracking_number}\n\n"
        f"Как только перевозчик обработает отправление, отслеживание начнет обновляться."
    )

    await m.answer(f"Трек для {order_no} сохранен и отправлен клиенту.")


@router.message(F.text)
async def sales_dialog(m: Message):
    if not m.from_user:
        return
    if _is_admin(m.from_user.id):
        return

    s = await get_sales_session(m.from_user.id)
    if not s:
        return

    text = (m.text or "").strip()
    if not text:
        return

    psychotype = s.get("psychotype", "")
    psychotype_conf = float(s.get("psychotype_conf") or 0)
    detected_psychotype, detected_conf = _detect_psychotype(text)
    if detected_conf > psychotype_conf:
        psychotype = detected_psychotype
        psychotype_conf = detected_conf

    context: dict[str, Any] = s.get("context", {}) or {}
    stage = s.get("stage", "profiling")
    sku = s.get("sku", "")

    if stage in ("profiling", "selling"):
        if _buyer_ready_to_checkout(text):
            await upsert_sales_session(
                user_id=m.from_user.id,
                sku=sku,
                stage="collect_size",
                psychotype=psychotype,
                psychotype_conf=psychotype_conf,
                context=context,
            )
            return await m.answer("Отлично, оформляем. Напишите, пожалуйста, нужный размер.")

        intro = _manager_style_intro(psychotype)
        await upsert_sales_session(
            user_id=m.from_user.id,
            sku=sku,
            stage="selling",
            psychotype=psychotype,
            psychotype_conf=psychotype_conf,
            context=context,
        )
        return await m.answer(
            f"{intro}\n\n"
            f"Я помогу по товару {sku}. Если готовы переходить к покупке, "
            f"просто напишите: «оформляем» или нажмите кнопку «Оформить заказ»."
        )

    if stage == "collect_size":
        context["size"] = text
        await upsert_sales_session(
            user_id=m.from_user.id,
            sku=sku,
            stage="collect_color",
            psychotype=psychotype,
            psychotype_conf=psychotype_conf,
            context=context,
        )
        return await m.answer("Отлично. Теперь напишите нужный цвет.")

    if stage == "collect_color":
        context["color"] = text
        await upsert_sales_session(
            user_id=m.from_user.id,
            sku=sku,
            stage="collect_name",
            psychotype=psychotype,
            psychotype_conf=psychotype_conf,
            context=context,
        )
        return await m.answer("Подскажите, пожалуйста, как к вам обращаться?")

    if stage == "collect_name":
        context["customer_name"] = text
        await upsert_sales_session(
            user_id=m.from_user.id,
            sku=sku,
            stage="collect_phone",
            psychotype=psychotype,
            psychotype_conf=psychotype_conf,
            context=context,
        )
        return await m.answer("Оставьте номер телефона для связи по заказу.")

    if stage == "collect_phone":
        context["customer_phone"] = text

        product = await get_product(sku)
        title = product.get("title", "") if product else sku
        price = float(product.get("price", 0)) if product else 0
        currency = product.get("currency", "RUB") if product else "RUB"

        temp_order = await create_sales_order(
            user_id=m.from_user.id,
            sku=sku,
            title=title,
            price=price,
            currency=currency,
            size=context.get("size", ""),
            color=context.get("color", ""),
            customer_name=context.get("customer_name", ""),
            customer_phone=context.get("customer_phone", ""),
            comment="Оформление через бота-менеджера",
            psychotype=psychotype,
            payment_url="",
            stage="waiting_payment",
        )

        payment_url = PAYMENT_URL_TEMPLATE.format(order_no=temp_order["order_no"])

        await _notify_admins(
            m.bot,
            "Новый заказ через бота\n\n"
            f"Номер заказа: {temp_order['order_no']}\n"
            f"User ID: {m.from_user.id}\n"
            f"SKU: {sku}\n"
            f"Товар: {title}\n"
            f"Размер: {context.get('size', '')}\n"
            f"Цвет: {context.get('color', '')}\n"
            f"Имя: {context.get('customer_name', '')}\n"
            f"Телефон: {context.get('customer_phone', '')}\n"
            f"Психотип: {psychotype or '-'}\n"
            f"Ссылка на оплату: {payment_url}"
        )

        await upsert_sales_session(
            user_id=m.from_user.id,
            sku=sku,
            stage="waiting_payment",
            psychotype=psychotype,
            psychotype_conf=psychotype_conf,
            context={**context, "order_no": temp_order["order_no"]},
        )

        return await m.answer(
            "Отлично, заказ почти оформлен ✅\n\n"
            f"Номер заказа: {temp_order['order_no']}\n"
            f"Товар: {title}\n"
            f"Размер: {context.get('size', '')}\n"
            f"Цвет: {context.get('color', '')}\n"
            f"Сумма: {_format_price(price)} {'₽' if currency.upper() == 'RUB' else currency}\n\n"
            "Сейчас этап оплаты. Пока используем тестовую логику, "
            "поэтому ссылку на эскроу позже просто подставим в это место.\n\n"
            f"Ссылка на оплату: {payment_url}\n\n"
            "После подтверждения оплаты я сразу сообщу статус заказа."
        )

    if stage == "waiting_payment":
        return await m.answer(
            "Ваш заказ уже создан и ожидает подтверждения оплаты.\n"
            "Как только оплата будет подтверждена, я сразу сообщу об этом здесь."
        )

    if stage == "packing":
        return await m.answer(
            "Ваш заказ уже в сборке. После передачи в доставку я пришлю трек-номер в этот чат."
        )

    if stage == "shipped":
        return await m.answer(
            "Заказ уже отправлен. Если хотите, я могу ещё раз продублировать трек-номер."
        )
