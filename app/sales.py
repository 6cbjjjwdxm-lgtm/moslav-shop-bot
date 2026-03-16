from __future__ import annotations

import json
import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import settings
from .db import (
    clear_sales_session,
    create_sales_order,
    get_conversation,
    get_product,
    get_sales_order_by_no,
    get_sales_session,
    set_sales_order_tracking,
    update_sales_order_stage,
    upsert_conversation,
    upsert_sales_session,
)
from .llm import SALES_SYSTEM_PROMPT_TEMPLATE, sales_chat
from .profiling import (
    detect_psychotype,
    estimate_purchase_readiness,
    extract_lead_context,
    get_style,
)
from .sizing import (
    extract_body_params,
    missing_params_question,
    recommend_size,
)

router = Router(name="sales")

MY_ADMIN_ID = 459980503
ALL_ADMIN_IDS = list(set([MY_ADMIN_ID, *list(getattr(settings, "admin_id_set", set()))]))

PAYMENT_URL_TEMPLATE = "https://example.com/pay/{order_no}"

STAGE_DESCRIPTIONS = {
    "new_chat": "Новый чат — покупатель только пришёл, нужно поприветствовать и выяснить потребность.",
    "profiling": "Профилирование — выясняем потребность: для кого, повод, бюджет, стиль, размер.",
    "selling": "Продажа — покупатель заинтересован, подбираем варианты, работаем с возражениями, ведём к покупке.",
    "collect_size": "Сбор размера — покупатель готов оформить, уточняем размер.",
    "collect_color": "Сбор цвета — размер выбран, уточняем цвет.",
    "collect_name": "Сбор имени — уточняем имя для заказа.",
    "collect_phone": "Сбор телефона — уточняем телефон для связи.",
    "waiting_payment": "Ожидание оплаты — заказ создан, ждём подтверждения оплаты.",
    "packing": "Сборка — заказ оплачен, собирается.",
    "shipped": "Отправлен — заказ передан в доставку.",
}


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


def _product_preview_text(p: dict) -> str:
    title = (p.get("title") or "").strip() or p.get("sku", "")
    price = _format_price(p.get("price", 0))
    currency = "₽" if (p.get("currency") or "RUB").upper() == "RUB" else (p.get("currency") or "")
    desc = (p.get("description") or "").strip()
    parts = [f"🛍 {title}"]
    parts.append(f"🏷️ Артикул: {p.get('sku', '')}")
    if desc:
        parts.append(desc[:200])
    parts.append(f"💰 Цена: {price} {currency}")

    sizes = p.get("sizes", [])
    if sizes:
        size_list = [s["size"] if isinstance(s, dict) else s for s in sizes if (isinstance(s, dict) and s.get("is_active", True)) or isinstance(s, str)]
        if size_list:
            parts.append(f"📏 Размеры: {', '.join(size_list)}")

    colors = p.get("colors", [])
    if colors:
        color_list = [c["color"] if isinstance(c, dict) else c for c in colors if (isinstance(c, dict) and c.get("is_active", True)) or isinstance(c, str)]
        if color_list:
            parts.append(f"🎨 Цвета: {', '.join(color_list)}")

    return "\n".join(parts)


def _product_info_for_prompt(p: dict) -> str:
    if not p:
        return "Товар не найден в каталоге."
    title = (p.get("title") or "").strip() or p.get("sku", "")
    lines = [f"Название: {title}", f"Артикул: {p.get('sku', '')}"]
    if p.get("description"):
        lines.append(f"Описание: {p['description'][:300]}")
    lines.append(f"Цена: {_format_price(p.get('price', 0))} {p.get('currency', 'RUB')}")
    if p.get("category"):
        lines.append(f"Категория: {p['category']}")
    if p.get("gender"):
        lines.append(f"Пол: {p['gender']}")
    if p.get("season"):
        lines.append(f"Сезон: {p['season']}")
    if p.get("material"):
        lines.append(f"Материал: {p['material']}")
    if p.get("insulation"):
        lines.append(f"Утеплитель: {p['insulation']}")

    sizes = p.get("sizes", [])
    if sizes:
        size_list = [s["size"] if isinstance(s, dict) else s for s in sizes if (isinstance(s, dict) and s.get("is_active", True)) or isinstance(s, str)]
        if size_list:
            lines.append(f"Доступные размеры: {', '.join(size_list)}")

    colors = p.get("colors", [])
    if colors:
        color_list = [c["color"] if isinstance(c, dict) else c for c in colors if (isinstance(c, dict) and c.get("is_active", True)) or isinstance(c, str)]
        if color_list:
            lines.append(f"Доступные цвета: {', '.join(color_list)}")

    if p.get("is_sale"):
        lines.append("⚡ Товар по акции")
    return "\n".join(lines)


def _lead_context_for_prompt(ctx: dict) -> str:
    if not ctx:
        return "Контекст ещё не собран."
    parts = []
    if ctx.get("gender"):
        parts.append(f"Пол: {ctx['gender']}")
    if ctx.get("budget"):
        parts.append(f"Бюджет: до {ctx['budget']} руб.")
    if ctx.get("season_pref"):
        parts.append(f"Сезон: {ctx['season_pref']}")
    if ctx.get("occasion"):
        parts.append(f"Повод: {ctx['occasion']}")
    if ctx.get("fit_pref"):
        parts.append(f"Посадка: {ctx['fit_pref']}")
    if ctx.get("urgency"):
        parts.append(f"Срочность: {ctx['urgency']}")
    if ctx.get("category_interest"):
        parts.append(f"Интерес к категории: {ctx['category_interest']}")
    if ctx.get("color_pref"):
        parts.append(f"Предпочтение по цвету: {ctx['color_pref']}")
    return "\n".join(parts) if parts else "Контекст ещё не собран."


def _sizing_info_for_prompt(ctx: dict, product: dict | None) -> str:
    body = ctx.get("body_params", {})
    if not body:
        return "Параметры тела не указаны. Если покупатель сомневается в размере, спроси рост и вес."

    gender = ctx.get("gender", product.get("gender", "male") if product else "male")
    fit = ctx.get("fit_pref", "")
    available = None
    if product:
        sizes = product.get("sizes", [])
        available = [s["size"] if isinstance(s, dict) else s for s in sizes if (isinstance(s, dict) and s.get("is_active", True)) or isinstance(s, str)]

    rec = recommend_size(body, gender=gender, fit_pref=fit, available_sizes=available)
    if not rec:
        return f"Есть параметры ({body}), но недостаточно данных для рекомендации. Уточни обхваты."

    parts = [
        f"Рекомендуемый размер: {rec.primary} (уверенность {rec.confidence:.0%})",
        f"Альтернатива: {rec.alternative}",
    ]
    if rec.note:
        parts.append(f"Примечание: {rec.note}")
    return "\n".join(parts)


def _build_sales_prompt(
    psychotype: str,
    psychotype_conf: float,
    context: dict,
    product: dict | None,
    stage: str,
) -> str:
    style = get_style(psychotype)
    return SALES_SYSTEM_PROMPT_TEMPLATE.format(
        psychotype=psychotype or "silent",
        psychotype_conf=psychotype_conf,
        tone=style.get("tone", ""),
        length=style.get("length", ""),
        arguments=style.get("arguments", ""),
        closing=style.get("closing", ""),
        objections=style.get("objections", ""),
        lead_context=_lead_context_for_prompt(context),
        product_info=_product_info_for_prompt(product) if product else "Товар не выбран.",
        stage_description=STAGE_DESCRIPTIONS.get(stage, stage),
        sizing_info=_sizing_info_for_prompt(context, product),
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


def _buyer_ready_to_checkout(text: str) -> bool:
    t = (text or "").lower()
    triggers = [
        "беру", "оформ", "хочу купить", "покупаю", "заказываю",
        "куда платить", "как оплатить", "готов оплатить", "оплатить",
    ]
    return any(x in t for x in triggers)


# -------- Handlers --------

@router.message(Command("start"))
async def sales_start(m: Message):
    if not m.from_user:
        return

    start_param = _parse_start_param(m.text or "")
    if not start_param.startswith("manager_"):
        return

    sku = start_param.removeprefix("manager_").strip()

    await upsert_sales_session(
        user_id=m.from_user.id,
        sku=sku,
        stage="profiling",
        psychotype="",
        psychotype_conf=0,
        context={},
    )
    # Reset sales conversation history
    await upsert_conversation(m.from_user.id, [])

    await m.answer("Здравствуйте! Я менеджер магазина и помогу подобрать и оформить заказ.")
    await _send_product_preview(m, sku)


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
    psychotype = s.get("psychotype", "")
    psychotype_conf = float(s.get("psychotype_conf") or 0)
    sku = s.get("sku", "")

    # Check if we have sizing info — try to recommend
    product = await get_product(sku) if sku else None
    body = context.get("body_params", {})
    gender = context.get("gender", product.get("gender", "male") if product else "male")
    available = None
    if product:
        sizes = product.get("sizes", [])
        available = [sv["size"] if isinstance(sv, dict) else sv for sv in sizes if (isinstance(sv, dict) and sv.get("is_active", True)) or isinstance(sv, str)]

    rec = recommend_size(body, gender=gender, fit_pref=context.get("fit_pref", ""), available_sizes=available) if body else None

    if rec and rec.confidence >= 0.5:
        # Pre-fill recommended size
        context["recommended_size"] = rec.primary
        context["recommended_alt"] = rec.alternative
        await upsert_sales_session(
            user_id=cb.from_user.id, sku=sku, stage="collect_size",
            psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
        )
        await cb.answer()
        if cb.message:
            await cb.message.answer(
                f"По вашим параметрам рекомендую размер {rec.primary} "
                f"(уверенность {rec.confidence:.0%}).\n"
                f"Альтернатива: {rec.alternative}.\n\n"
                "Напишите нужный размер или подтвердите рекомендованный."
            )
    else:
        await upsert_sales_session(
            user_id=cb.from_user.id, sku=sku, stage="collect_size",
            psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
        )
        await cb.answer()
        if cb.message:
            sizes_text = ""
            if available:
                sizes_text = f"\nДоступные размеры: {', '.join(available)}"
            await cb.message.answer(f"Отлично, оформляем. Напишите нужный размер.{sizes_text}")


# -------- Admin commands --------

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


@router.message(Command("lead"))
async def admin_lead(m: Message):
    """Admin command: show lead intelligence for a user."""
    if not m.from_user or not _is_admin(m.from_user.id):
        return

    parts = (m.text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        return await m.answer("Формат: /lead <user_id>")

    try:
        target_id = int(parts[1].strip())
    except ValueError:
        return await m.answer("user_id должен быть числом.")

    s = await get_sales_session(target_id)
    if not s:
        return await m.answer(f"Сессия для user_id={target_id} не найдена.")

    ctx = s.get("context", {})
    psychotype = s.get("psychotype", "") or "не определён"
    psychotype_conf = float(s.get("psychotype_conf") or 0)
    stage = s.get("stage", "")
    sku = s.get("sku", "")

    readiness = estimate_purchase_readiness("", ctx, stage)

    style = get_style(psychotype if psychotype != "не определён" else "silent")

    body = ctx.get("body_params", {})
    sizing_text = "нет данных"
    if body:
        product = await get_product(sku) if sku else None
        gender = ctx.get("gender", product.get("gender", "male") if product else "male")
        rec = recommend_size(body, gender=gender, fit_pref=ctx.get("fit_pref", ""))
        if rec:
            sizing_text = f"{rec.primary} ({rec.confidence:.0%}), альт: {rec.alternative}"

    lines = [
        f"📊 Лид: {target_id}",
        f"SKU: {sku or '-'}",
        f"Стадия: {stage}",
        f"Психотип: {psychotype} ({psychotype_conf:.0%})",
        f"Тон: {style.get('tone', '-')}",
        f"Готовность к покупке: {readiness:.0%}",
        "",
        "Контекст:",
        f"  Пол: {ctx.get('gender', '-')}",
        f"  Бюджет: {ctx.get('budget', '-')}",
        f"  Сезон: {ctx.get('season_pref', '-')}",
        f"  Повод: {ctx.get('occasion', '-')}",
        f"  Посадка: {ctx.get('fit_pref', '-')}",
        f"  Срочность: {ctx.get('urgency', '-')}",
        f"  Категория: {ctx.get('category_interest', '-')}",
        f"  Цвет: {ctx.get('color_pref', '-')}",
        "",
        f"Размер: {sizing_text}",
        f"Параметры тела: {body if body else '-'}",
    ]

    await m.answer("\n".join(lines))


# -------- Main sales dialog --------

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

    user_id = m.from_user.id
    psychotype = s.get("psychotype", "")
    psychotype_conf = float(s.get("psychotype_conf") or 0)
    context: dict[str, Any] = s.get("context", {}) or {}
    stage = s.get("stage", "profiling")
    sku = s.get("sku", "")

    # --- Update profiling signals from every message ---
    history = await get_conversation(user_id) or []

    # Detect psychotype (uses full history)
    new_psychotype, new_conf = detect_psychotype(
        text, history=history,
        current_psychotype=psychotype, current_conf=psychotype_conf,
    )
    psychotype = new_psychotype
    psychotype_conf = new_conf

    # Extract lead context
    context = extract_lead_context(text, existing=context)

    # Extract body params for sizing
    body_params = extract_body_params(text, existing=context.get("body_params"))
    if body_params:
        context["body_params"] = body_params

    # --- Stage-specific logic ---

    if stage in ("profiling", "selling"):
        # Check if buyer wants to checkout
        if _buyer_ready_to_checkout(text):
            await upsert_sales_session(
                user_id=user_id, sku=sku, stage="collect_size",
                psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
            )

            # Try size recommendation
            product = await get_product(sku) if sku else None
            body = context.get("body_params", {})
            if body:
                gender = context.get("gender", product.get("gender", "male") if product else "male")
                available = None
                if product:
                    sizes = product.get("sizes", [])
                    available = [sv["size"] if isinstance(sv, dict) else sv for sv in sizes if (isinstance(sv, dict) and sv.get("is_active", True)) or isinstance(sv, str)]
                rec = recommend_size(body, gender=gender, fit_pref=context.get("fit_pref", ""), available_sizes=available)
                if rec and rec.confidence >= 0.5:
                    context["recommended_size"] = rec.primary
                    await upsert_sales_session(
                        user_id=user_id, sku=sku, stage="collect_size",
                        psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
                    )
                    return await m.answer(
                        f"Отлично, оформляем! По вашим параметрам рекомендую размер {rec.primary} "
                        f"(уверенность {rec.confidence:.0%}), альтернатива: {rec.alternative}.\n\n"
                        "Напишите нужный размер или подтвердите рекомендованный."
                    )

            return await m.answer("Отлично, оформляем. Напишите, пожалуйста, нужный размер.")

        # LLM-driven natural dialogue for profiling/selling
        product = await get_product(sku) if sku else None
        system_prompt = _build_sales_prompt(
            psychotype=psychotype,
            psychotype_conf=psychotype_conf,
            context=context,
            product=product,
            stage=stage,
        )

        # Add user message to conversation history
        history.append({"role": "user", "content": text})
        history = history[-20:]

        reply = await sales_chat(user_id=user_id, messages=history, system_prompt=system_prompt)

        history.append({"role": "assistant", "content": reply})
        await upsert_conversation(user_id, history)

        # Move to selling after first exchange
        new_stage = "selling" if stage == "profiling" else stage
        readiness = estimate_purchase_readiness(text, context, new_stage)

        await upsert_sales_session(
            user_id=user_id, sku=sku, stage=new_stage,
            psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
        )

        # Notify admins on high readiness
        if readiness >= 0.7:
            await _notify_admins(
                m.bot,
                f"🔥 Горячий лид!\n"
                f"User: {user_id}\n"
                f"SKU: {sku}\n"
                f"Готовность: {readiness:.0%}\n"
                f"Психотип: {psychotype} ({psychotype_conf:.0%})\n"
                f"Стадия: {new_stage}"
            )

        return await m.answer(reply)

    if stage == "collect_size":
        context["size"] = text
        await upsert_sales_session(
            user_id=user_id, sku=sku, stage="collect_color",
            psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
        )
        # Show available colors if known
        product = await get_product(sku) if sku else None
        colors_text = ""
        if product:
            colors = product.get("colors", [])
            color_list = [c["color"] if isinstance(c, dict) else c for c in colors if (isinstance(c, dict) and c.get("is_active", True)) or isinstance(c, str)]
            if color_list:
                colors_text = f"\nДоступные цвета: {', '.join(color_list)}"
        return await m.answer(f"Размер {text} — записал. Теперь напишите нужный цвет.{colors_text}")

    if stage == "collect_color":
        context["color"] = text
        await upsert_sales_session(
            user_id=user_id, sku=sku, stage="collect_name",
            psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
        )
        return await m.answer("Подскажите, пожалуйста, как к вам обращаться?")

    if stage == "collect_name":
        context["customer_name"] = text
        await upsert_sales_session(
            user_id=user_id, sku=sku, stage="collect_phone",
            psychotype=psychotype, psychotype_conf=psychotype_conf, context=context,
        )
        return await m.answer("Оставьте номер телефона для связи по заказу.")

    if stage == "collect_phone":
        context["customer_phone"] = text

        product = await get_product(sku)
        title = product.get("title", "") if product else sku
        price = float(product.get("price", 0)) if product else 0
        currency = product.get("currency", "RUB") if product else "RUB"

        temp_order = await create_sales_order(
            user_id=user_id,
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

        # Rich admin notification with lead intelligence
        readiness = estimate_purchase_readiness(text, context, "waiting_payment")
        style = get_style(psychotype)
        body = context.get("body_params", {})
        sizing_text = "-"
        if body:
            gender = context.get("gender", product.get("gender", "male") if product else "male")
            rec = recommend_size(body, gender=gender, fit_pref=context.get("fit_pref", ""))
            if rec:
                sizing_text = f"{rec.primary} ({rec.confidence:.0%})"

        await _notify_admins(
            m.bot,
            f"🛒 Новый заказ через бота\n\n"
            f"Номер: {temp_order['order_no']}\n"
            f"User ID: {user_id}\n"
            f"SKU: {sku}\n"
            f"Товар: {title}\n"
            f"Размер: {context.get('size', '')}\n"
            f"Цвет: {context.get('color', '')}\n"
            f"Имя: {context.get('customer_name', '')}\n"
            f"Телефон: {context.get('customer_phone', '')}\n\n"
            f"📊 Аналитика:\n"
            f"Психотип: {psychotype or '-'} ({psychotype_conf:.0%})\n"
            f"Тон: {style.get('tone', '-')}\n"
            f"Готовность: {readiness:.0%}\n"
            f"Рекомендация размера: {sizing_text}\n"
            f"Бюджет: {context.get('budget', '-')}\n"
            f"Повод: {context.get('occasion', '-')}\n"
            f"Ссылка на оплату: {payment_url}",
        )

        await upsert_sales_session(
            user_id=user_id, sku=sku, stage="waiting_payment",
            psychotype=psychotype, psychotype_conf=psychotype_conf,
            context={**context, "order_no": temp_order["order_no"]},
        )

        return await m.answer(
            f"Отлично, заказ почти оформлен ✅\n\n"
            f"Номер заказа: {temp_order['order_no']}\n"
            f"Товар: {title}\n"
            f"Размер: {context.get('size', '')}\n"
            f"Цвет: {context.get('color', '')}\n"
            f"Сумма: {_format_price(price)} {'₽' if currency.upper() == 'RUB' else currency}\n\n"
            f"Сейчас этап оплаты. Пока используем тестовую логику, "
            f"поэтому ссылку на эскроу позже просто подставим в это место.\n\n"
            f"Ссылка на оплату: {payment_url}\n\n"
            f"После подтверждения оплаты я сразу сообщу статус заказа."
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
