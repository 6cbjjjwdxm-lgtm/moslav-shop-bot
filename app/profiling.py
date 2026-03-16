"""
Buyer profiling: psychotype detection and lead context extraction.

Psychotypes (sales segmentation, not medical):
- rational: analytical, wants specs/price/quality facts
- decisive: ready to buy fast, hates delays
- emotional: buys on feeling, aesthetics, impulse
- cautious: needs reassurance, fears mistakes
- status: brand/exclusivity driven, wants premium feel
- silent: short answers, needs gentle drawing out
"""

from __future__ import annotations

import re
from typing import Any

PSYCHOTYPE_KEYWORDS: dict[str, list[str]] = {
    "rational": [
        "цена", "состав", "материал", "размер", "доставка", "сколько",
        "качество", "характеристик", "сравни", "аналог", "отличи",
        "параметр", "точн", "конкретн", "факт", "данн",
    ],
    "decisive": [
        "беру", "оформляем", "оплатить", "куплю", "сейчас", "срочно",
        "быстр", "давай", "го", "скорей", "времени нет", "не тяни",
    ],
    "emotional": [
        "красиво", "стильно", "нравится", "вау", "хочу", "люблю",
        "огонь", "круто", "обалде", "мечта", "кайф", "супер",
        "потряса", "восторг", "прикольн",
    ],
    "cautious": [
        "гарантия", "возврат", "точно", "если", "вдруг", "переживаю",
        "надежно", "уверен", "обман", "боюсь", "сомнева", "можно ли",
        "а если", "не подойдет", "риск",
    ],
    "status": [
        "бренд", "оригинал", "лимитк", "эксклюзив", "премиум",
        "лучший", "топ", "элитн", "статус", "дорог", "понт",
        "престиж", "уникальн",
    ],
}

# Tone/style presets per psychotype
PSYCHOTYPE_STYLE: dict[str, dict[str, str]] = {
    "rational": {
        "tone": "деловой, фактологический",
        "length": "средняя, по делу",
        "arguments": "цифры, состав, сравнение, логика выгоды",
        "closing": "резюме характеристик + прямой вопрос о заказе",
        "objections": "факты и сравнения, без эмоций",
        "greeting": "Понял вас. Коротко и по делу:",
    },
    "decisive": {
        "tone": "энергичный, быстрый",
        "length": "короткая, без лишнего",
        "arguments": "наличие, скорость доставки, простота оформления",
        "closing": "сразу к оформлению, минимум шагов",
        "objections": "быстрое снятие сомнения + конкретный следующий шаг",
        "greeting": "Отлично, идем быстро и по шагам.",
    },
    "emotional": {
        "tone": "теплый, вдохновляющий",
        "length": "средняя, с яркими описаниями",
        "arguments": "как вещь выглядит, ощущается, подчеркивает стиль",
        "closing": "визуализация образа + мягкий импульс к покупке",
        "objections": "подтвердить чувства, предложить альтернативу с эмоцией",
        "greeting": "Отличный выбор — модель действительно цепляет!",
    },
    "cautious": {
        "tone": "спокойный, надежный, участливый",
        "length": "подробная, с пояснениями",
        "arguments": "гарантия возврата, отзывы, проверенное качество",
        "closing": "мягкое предложение попробовать без риска",
        "objections": "понимание опасений + конкретные гарантии",
        "greeting": "Понимаю ваш вопрос. Давайте спокойно разберёмся:",
    },
    "status": {
        "tone": "уважительный, с нотой эксклюзивности",
        "length": "средняя, подчеркивающая ценность",
        "arguments": "уникальность, качество пошива, ограниченность",
        "closing": "эксклюзивность + ограниченная доступность",
        "objections": "подтверждение ценности, сравнение с масс-маркетом",
        "greeting": "Рад, что вы обратили внимание — вещь для ценителей.",
    },
    "silent": {
        "tone": "дружелюбный, ненавязчивый",
        "length": "короткая, с мягкими вопросами",
        "arguments": "простые, по одному за раз",
        "closing": "мягкий вопрос-предложение",
        "objections": "не давить, предложить подумать и вернуться",
        "greeting": "С радостью помогу. Расскажите, что ищете?",
    },
}


def detect_psychotype(
    text: str,
    history: list[dict[str, str]] | None = None,
    current_psychotype: str = "",
    current_conf: float = 0.0,
) -> tuple[str, float]:
    all_text = (text or "").lower()
    if history:
        user_msgs = " ".join(
            m.get("content", "") for m in history if m.get("role") == "user"
        )
        all_text = f"{user_msgs} {all_text}".lower()

    scores: dict[str, int] = {}
    for ptype, keywords in PSYCHOTYPE_KEYWORDS.items():
        score = sum(1 for w in keywords if w in all_text)
        scores[ptype] = score

    total_hits = sum(scores.values())
    if total_hits == 0:
        if current_psychotype:
            return current_psychotype, current_conf
        return "silent", 0.3

    best = max(scores, key=lambda k: scores[k])
    best_score = scores[best]
    conf = min(0.95, 0.3 + best_score * 0.12)

    # If message count is high but psychotype is low-confidence, might be silent
    if history and len([m for m in history if m.get("role") == "user"]) >= 4 and best_score <= 1:
        if current_psychotype == "silent":
            return "silent", min(0.7, current_conf + 0.1)
        return "silent", 0.4

    if conf > current_conf:
        return best, conf
    return current_psychotype or best, max(current_conf, conf)


def get_style(psychotype: str) -> dict[str, str]:
    return PSYCHOTYPE_STYLE.get(psychotype, PSYCHOTYPE_STYLE["silent"])


def extract_lead_context(text: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract lead context signals from user message text."""
    ctx = dict(existing or {})
    t = (text or "").lower()

    # Gender detection
    if not ctx.get("gender"):
        if any(w in t for w in ["для парня", "мужск", "мужчин", "себе парню", "мужу", "брату"]):
            ctx["gender"] = "male"
        elif any(w in t for w in ["для девушк", "женск", "женщин", "себе девушке", "жене", "сестр", "подруг"]):
            ctx["gender"] = "female"

    # Budget detection
    if not ctx.get("budget"):
        budget_match = re.search(r"до\s*(\d[\d\s]*)\s*(?:руб|₽|р\.?|тыс)?", t)
        if budget_match:
            raw = budget_match.group(1).replace(" ", "")
            try:
                val = int(raw)
                if val < 100:
                    val *= 1000
                ctx["budget"] = val
            except ValueError:
                pass

    # Season
    if not ctx.get("season_pref"):
        if any(w in t for w in ["зим", "холод", "мороз", "тепл"]):
            ctx["season_pref"] = "winter"
        elif any(w in t for w in ["лет", "жар", "легк"]):
            ctx["season_pref"] = "summer"
        elif any(w in t for w in ["весн", "осен", "демисезон"]):
            ctx["season_pref"] = "autumn"

    # Occasion
    if not ctx.get("occasion"):
        if any(w in t for w in ["подарок", "подарить", "дарить"]):
            ctx["occasion"] = "gift"
        elif any(w in t for w in ["на каждый день", "повседн", "на работу"]):
            ctx["occasion"] = "everyday"
        elif any(w in t for w in ["спорт", "трениров", "бег", "зал"]):
            ctx["occasion"] = "sport"
        elif any(w in t for w in ["прогулк", "город", "улиц"]):
            ctx["occasion"] = "casual"

    # Fit preference
    if not ctx.get("fit_pref"):
        if any(w in t for w in ["оверсайз", "oversize", "свободн", "оверс"]):
            ctx["fit_pref"] = "oversize"
        elif any(w in t for w in ["по фигуре", "облегающ", "slim", "приталенн"]):
            ctx["fit_pref"] = "slim"
        elif any(w in t for w in ["обычн", "regular", "стандарт"]):
            ctx["fit_pref"] = "regular"

    # Urgency
    if not ctx.get("urgency"):
        if any(w in t for w in ["срочно", "сегодня", "завтра", "быстрее", "скорей"]):
            ctx["urgency"] = "high"
        elif any(w in t for w in ["не спеш", "когда будет", "подожду"]):
            ctx["urgency"] = "low"

    # Category interest
    if not ctx.get("category_interest"):
        cat_map = {
            "hoodie": ["худи", "толстовк", "свитшот", "кенгур"],
            "jacket": ["куртк", "пуховик", "бомбер", "ветровк"],
            "pants": ["штан", "брюк", "джогер", "карго"],
            "tshirt": ["футболк", "майк", "тишк"],
            "shorts": ["шорт"],
            "tracksuit": ["костюм", "спортивн"],
            "anorak": ["анорак"],
            "vest": ["безрукавк", "жилет"],
        }
        for cat, words in cat_map.items():
            if any(w in t for w in words):
                ctx["category_interest"] = cat
                break

    # Color preference
    if not ctx.get("color_pref"):
        color_words = [
            "черн", "бел", "серы", "серо", "синий", "синюю", "красн",
            "зелен", "хаки", "бежев", "коричнев", "голуб",
        ]
        for w in color_words:
            if w in t:
                ctx["color_pref"] = w.rstrip("нйюыоае")
                break

    return ctx


def estimate_purchase_readiness(
    text: str,
    context: dict[str, Any],
    stage: str,
) -> float:
    """Score 0.0-1.0 how close the lead is to buying."""
    score = 0.0
    t = (text or "").lower()

    # Direct buy signals
    buy_words = ["беру", "оформ", "хочу купить", "покупаю", "заказываю",
                 "куда платить", "как оплатить", "готов оплатить", "оплатить"]
    if any(w in t for w in buy_words):
        score += 0.5

    # Context completeness
    if context.get("gender"):
        score += 0.05
    if context.get("budget"):
        score += 0.05
    if context.get("category_interest"):
        score += 0.1
    if context.get("color_pref"):
        score += 0.05
    if context.get("fit_pref"):
        score += 0.05

    # Stage progression
    stage_scores = {
        "new_chat": 0.0,
        "profiling": 0.05,
        "selling": 0.15,
        "collect_size": 0.4,
        "collect_color": 0.5,
        "collect_name": 0.7,
        "collect_phone": 0.85,
        "waiting_payment": 0.95,
    }
    score += stage_scores.get(stage, 0.0)

    return min(1.0, score)
