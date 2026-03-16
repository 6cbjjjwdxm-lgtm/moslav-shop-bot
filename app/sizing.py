"""
Size recommendation engine.

Uses height/weight/chest/waist + fit preference to recommend a size.
Size charts are extensible per category. If product has its own chart
(future), it takes priority over defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SizeRecommendation:
    primary: str
    confidence: float  # 0.0–1.0
    alternative: str
    note: str


# Default size charts: maps (category, gender) → list of (size, min_chest, max_chest, min_waist, max_waist)
# Chest and waist in cm. Ranges overlap for borderline cases.
DEFAULT_CHARTS: dict[str, list[dict[str, Any]]] = {
    "male": [
        {"size": "S",    "chest_min": 86,  "chest_max": 92,  "waist_min": 70, "waist_max": 78,  "height_min": 165, "height_max": 175, "weight_min": 55, "weight_max": 68},
        {"size": "M",    "chest_min": 92,  "chest_max": 100, "waist_min": 78, "waist_max": 86,  "height_min": 170, "height_max": 180, "weight_min": 65, "weight_max": 78},
        {"size": "L",    "chest_min": 100, "chest_max": 108, "waist_min": 86, "waist_max": 94,  "height_min": 175, "height_max": 185, "weight_min": 75, "weight_max": 90},
        {"size": "XL",   "chest_min": 108, "chest_max": 116, "waist_min": 94, "waist_max": 102, "height_min": 178, "height_max": 190, "weight_min": 85, "weight_max": 100},
        {"size": "XXL",  "chest_min": 116, "chest_max": 124, "waist_min": 102,"waist_max": 110, "height_min": 180, "height_max": 195, "weight_min": 95, "weight_max": 115},
        {"size": "XXXL", "chest_min": 124, "chest_max": 136, "waist_min": 110,"waist_max": 124, "height_min": 182, "height_max": 200, "weight_min": 110,"weight_max": 140},
    ],
    "female": [
        {"size": "S",    "chest_min": 82,  "chest_max": 88,  "waist_min": 62, "waist_max": 68,  "height_min": 155, "height_max": 168, "weight_min": 45, "weight_max": 55},
        {"size": "M",    "chest_min": 88,  "chest_max": 96,  "waist_min": 68, "waist_max": 76,  "height_min": 160, "height_max": 173, "weight_min": 53, "weight_max": 65},
        {"size": "L",    "chest_min": 96,  "chest_max": 104, "waist_min": 76, "waist_max": 84,  "height_min": 163, "height_max": 178, "weight_min": 62, "weight_max": 78},
        {"size": "XL",   "chest_min": 104, "chest_max": 112, "waist_min": 84, "waist_max": 94,  "height_min": 165, "height_max": 182, "weight_min": 75, "weight_max": 92},
        {"size": "XXL",  "chest_min": 112, "chest_max": 120, "waist_min": 94, "waist_max": 104, "height_min": 168, "height_max": 185, "weight_min": 88, "weight_max": 110},
        {"size": "XXXL", "chest_min": 120, "chest_max": 132, "waist_min": 104,"waist_max": 118, "height_min": 170, "height_max": 190, "weight_min": 105,"weight_max": 135},
    ],
}

SIZE_ORDER = ["S", "M", "L", "XL", "XXL", "XXXL"]


def _size_index(s: str) -> int:
    try:
        return SIZE_ORDER.index(s.upper())
    except ValueError:
        return -1


def extract_body_params(text: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse height/weight/chest/waist from free-form text."""
    params = dict(existing or {})
    t = (text or "").lower()

    # Height
    h = re.search(r"рост\s*[:—–-]?\s*(\d{2,3})", t)
    if not h:
        h = re.search(r"(\d{3})\s*(?:см|ростом|рост)", t)
    if h:
        val = int(h.group(1))
        if 140 <= val <= 220:
            params["height"] = val

    # Weight
    w = re.search(r"вес\s*[:—–-]?\s*(\d{2,3})", t)
    if not w:
        w = re.search(r"(\d{2,3})\s*кг", t)
    if w:
        val = int(w.group(1))
        if 35 <= val <= 200:
            params["weight"] = val

    # Chest
    c = re.search(r"(?:грудь|ог|обхват\s*груди)\s*[:—–-]?\s*(\d{2,3})", t)
    if c:
        val = int(c.group(1))
        if 70 <= val <= 160:
            params["chest"] = val

    # Waist
    wa = re.search(r"(?:талия|от|обхват\s*талии)\s*[:—–-]?\s*(\d{2,3})", t)
    if wa:
        val = int(wa.group(1))
        if 55 <= val <= 150:
            params["waist"] = val

    return params


def recommend_size(
    params: dict[str, Any],
    gender: str = "male",
    fit_pref: str = "",
    available_sizes: list[str] | None = None,
) -> Optional[SizeRecommendation]:
    """Recommend size based on body params, gender, fit preference."""
    chart = DEFAULT_CHARTS.get(gender, DEFAULT_CHARTS["male"])
    height = params.get("height")
    weight = params.get("weight")
    chest = params.get("chest")
    waist = params.get("waist")

    if not height and not weight and not chest:
        return None

    # Score each size
    size_scores: list[tuple[str, float, int]] = []

    for entry in chart:
        score = 0.0
        matches = 0

        if chest:
            mid = (entry["chest_min"] + entry["chest_max"]) / 2
            rng = (entry["chest_max"] - entry["chest_min"]) / 2
            diff = abs(chest - mid)
            if diff <= rng:
                score += 3.0 * (1 - diff / max(rng, 1))
                matches += 1
            elif diff <= rng * 1.5:
                score += 1.0 * (1 - diff / (rng * 1.5))
                matches += 1

        if waist:
            mid = (entry["waist_min"] + entry["waist_max"]) / 2
            rng = (entry["waist_max"] - entry["waist_min"]) / 2
            diff = abs(waist - mid)
            if diff <= rng:
                score += 2.0 * (1 - diff / max(rng, 1))
                matches += 1
            elif diff <= rng * 1.5:
                score += 0.5 * (1 - diff / (rng * 1.5))
                matches += 1

        if height:
            mid = (entry["height_min"] + entry["height_max"]) / 2
            rng = (entry["height_max"] - entry["height_min"]) / 2
            diff = abs(height - mid)
            if diff <= rng:
                score += 1.5 * (1 - diff / max(rng, 1))
                matches += 1
            elif diff <= rng * 1.5:
                score += 0.3 * (1 - diff / (rng * 1.5))
                matches += 1

        if weight:
            mid = (entry["weight_min"] + entry["weight_max"]) / 2
            rng = (entry["weight_max"] - entry["weight_min"]) / 2
            diff = abs(weight - mid)
            if diff <= rng:
                score += 1.5 * (1 - diff / max(rng, 1))
                matches += 1
            elif diff <= rng * 1.5:
                score += 0.3 * (1 - diff / (rng * 1.5))
                matches += 1

        size_scores.append((entry["size"], score, matches))

    if not size_scores:
        return None

    size_scores.sort(key=lambda x: (-x[1], -x[2]))
    best_size = size_scores[0][0]
    best_score = size_scores[0][1]

    # Apply fit preference shift
    best_idx = _size_index(best_size)
    if fit_pref == "oversize" and best_idx < len(SIZE_ORDER) - 1:
        best_size = SIZE_ORDER[best_idx + 1]
    elif fit_pref == "slim" and best_idx > 0:
        best_size = SIZE_ORDER[best_idx - 1]

    # Calculate confidence
    max_possible = 8.0  # 3 + 2 + 1.5 + 1.5
    confidence = min(0.95, best_score / max_possible)
    if not chest and not waist:
        confidence = min(confidence, 0.6)

    # Alternative size
    alt_idx = _size_index(best_size)
    if alt_idx < len(SIZE_ORDER) - 1:
        alt_size = SIZE_ORDER[alt_idx + 1]
    elif alt_idx > 0:
        alt_size = SIZE_ORDER[alt_idx - 1]
    else:
        alt_size = best_size

    # Filter by available sizes
    if available_sizes:
        avail_upper = [s.upper() for s in available_sizes]
        if best_size not in avail_upper:
            # Find closest available
            for delta in [1, -1, 2, -2]:
                check_idx = _size_index(best_size) + delta
                if 0 <= check_idx < len(SIZE_ORDER) and SIZE_ORDER[check_idx] in avail_upper:
                    alt_size = best_size
                    best_size = SIZE_ORDER[check_idx]
                    confidence *= 0.8
                    break
        if alt_size not in avail_upper:
            alt_size = best_size

    # Note
    notes = []
    if confidence < 0.5:
        notes.append("рекомендация приблизительная — уточните обхваты для точного подбора")
    elif confidence < 0.7:
        notes.append("средняя уверенность — можно уточнить обхват груди")
    if fit_pref == "oversize":
        notes.append("размер увеличен с учётом свободной посадки")
    elif fit_pref == "slim":
        notes.append("размер уменьшен с учётом облегающей посадки")

    return SizeRecommendation(
        primary=best_size,
        confidence=round(confidence, 2),
        alternative=alt_size,
        note="; ".join(notes) if notes else "подходящий размер по параметрам",
    )


def missing_params_question(params: dict[str, Any], gender: str = "") -> Optional[str]:
    """Return the next question to ask for sizing, or None if we have enough."""
    if not params.get("height") and not params.get("weight"):
        return "Подскажите ваш рост и вес — так я смогу точнее подобрать размер."
    if not params.get("height"):
        return "Какой у вас рост?"
    if not params.get("weight"):
        return "Какой примерно вес?"
    # We have enough for a basic recommendation
    return None
