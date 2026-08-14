"""Normalize and compare Ozon product characteristics."""

from __future__ import annotations

import re
from typing import Mapping

from .models import MatchingSettings, ProductCharacteristics

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_PACKAGE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[xх×]\s*(\d+(?:[.,]\d+)?)\s*[xх×]\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_COMPARABLE_MATERIALS = frozenset({"лдсп", "пластик"})

_MATERIAL_ALIASES = {
    "дсп": "лдсп",
    "chipboard": "лдсп",
    "лдсп": "лдсп",
    "пластик": "пластик",
    "пластиковый": "пластик",
    "пластиковая": "пластик",
    "пластиковые": "пластик",
    "пвх": "пластик",
    "abs": "пластик",
    "абс": "пластик",
    "полипропилен": "пластик",
    "полистирол": "пластик",
    "полиэтилен": "пластик",
}


def rejection_labels(settings: MatchingSettings | None = None) -> dict[str, str]:
    rules = settings or MatchingSettings()
    return {
        "excluded": "артикул в справочнике исключений",
        "missing_materials": "нет ЛДСП или пластика",
        "materials_below_threshold": f"материалы ниже {rules.material_similarity:.0%}",
        "dimensions_too_far": (
            f"габариты отличаются более чем на {rules.max_dimension_difference:.0%}"
        ),
        "not_enough_dimensions": (
            f"меньше {rules.min_dimension_count} сопоставимых габаритов"
        ),
        "weight_too_far": f"вес отличается более чем на {rules.max_weight_difference:.0%}",
        "insufficient_data": "недостаточно сравнимых характеристик",
        "total_below_threshold": f"итоговое сходство ниже {rules.overall_similarity:.0%}",
        "rating_below_threshold": f"рейтинг ниже {rules.min_rating:g}",
        "feedbacks_below_threshold": f"отзывов меньше {rules.min_feedbacks}",
    }


REJECTION_LABELS = rejection_labels()


def extract_characteristics(raw: Mapping[str, str]) -> ProductCharacteristics:
    dimensions: dict[str, float] = {}
    weight: float | None = None
    materials: set[str] = set()

    for label, value in raw.items():
        normalized_label = _normalize(label)
        normalized_value = _normalize(value)

        if "размер упаков" in normalized_label:
            package = _PACKAGE_RE.search(normalized_value)
            if package:
                length, width, height = (_to_float(item) for item in package.groups())
                dimensions.update(
                    {
                        "длина упаковки": length,
                        "ширина упаковки": width,
                        "высота упаковки": height,
                    }
                )
                continue

        dimension_key = _dimension_key(normalized_label)
        if dimension_key:
            number = _first_number(normalized_value)
            if number is not None and number > 0:
                dimensions[dimension_key] = _dimension_to_cm(number, normalized_value)

        if "вес" in normalized_label or "масса" in normalized_label:
            parsed_weight = _weight_to_kg(normalized_value)
            if parsed_weight is not None:
                weight = parsed_weight

        if any(
            hint in normalized_label
            for hint in ("материал", "состав", "корпус", "фасад", "столешниц", "каркас")
        ):
            materials.update(_material_tokens(normalized_value))

    return ProductCharacteristics(
        dimensions_cm=dict(sorted(dimensions.items())),
        weight_kg=weight,
        materials=tuple(sorted(materials)),
        raw={str(key): str(value) for key, value in raw.items()},
    )


def evaluate_similarity(
    source: ProductCharacteristics,
    candidate: ProductCharacteristics,
    settings: MatchingSettings | None = None,
) -> tuple[float | None, tuple[str, ...], str]:
    rules = settings or MatchingSettings()
    rules.validate()

    source_materials = _comparison_materials(source.materials)
    candidate_materials = _comparison_materials(candidate.materials)
    if not source_materials or not candidate_materials:
        return None, (), "missing_materials"

    material_score = _dice_similarity(source_materials, candidate_materials)
    if material_score < rules.material_similarity:
        return None, (), "materials_below_threshold"

    weighted: list[tuple[float, float, str]] = [
        (material_score, rules.material_weight, f"материалы {material_score:.0%}")
    ]

    dimension_score, dimension_reason = _dimension_similarity(source, candidate, rules)
    if dimension_reason:
        return None, (), dimension_reason
    if dimension_score is not None and rules.dimensions_weight > 0:
        weighted.append((dimension_score, rules.dimensions_weight, "габариты"))

    if source.weight_kg is not None and candidate.weight_kg is not None:
        difference = _relative_difference(source.weight_kg, candidate.weight_kg)
        if difference > rules.max_weight_difference:
            return None, (), "weight_too_far"
        if rules.weight_weight > 0:
            weighted.append(
                (
                    max(0.0, 1.0 - difference / max(rules.max_weight_difference, 1e-9)),
                    rules.weight_weight,
                    "вес",
                )
            )

    positive_weighted = [item for item in weighted if item[1] > 0]
    if len(positive_weighted) == 1:
        return None, (), "insufficient_data"

    total_weight = sum(weight for _, weight, _ in positive_weighted)
    score = sum(value * weight for value, weight, _ in positive_weighted) / total_weight
    if score < rules.overall_similarity:
        return None, (), "total_below_threshold"
    return round(score, 4), tuple(label for _, _, label in positive_weighted), ""


def similarity(
    source: ProductCharacteristics,
    candidate: ProductCharacteristics,
    settings: MatchingSettings | None = None,
) -> tuple[float | None, tuple[str, ...]]:
    score, fields, _ = evaluate_similarity(source, candidate, settings)
    return score, fields


def format_dimensions(characteristics: ProductCharacteristics) -> str:
    return "; ".join(
        f"{name}: {value:g} см" for name, value in characteristics.dimensions_cm.items()
    )


def format_materials(characteristics: ProductCharacteristics) -> str:
    return ", ".join(sorted(_comparison_materials(characteristics.materials)))


def _dimension_similarity(
    source: ProductCharacteristics,
    candidate: ProductCharacteristics,
    rules: MatchingSettings,
) -> tuple[float | None, str]:
    source_item = _dimension_values(source.dimensions_cm, package=False)
    candidate_item = _dimension_values(candidate.dimensions_cm, package=False)
    source_package = _dimension_values(source.dimensions_cm, package=True)
    candidate_package = _dimension_values(candidate.dimensions_cm, package=True)

    pairs: list[tuple[float, float]] = []
    if source_item and candidate_item:
        count = min(len(source_item), len(candidate_item), 3)
        if count < rules.min_dimension_count:
            return None, "not_enough_dimensions"
        pairs = list(zip(sorted(source_item)[:count], sorted(candidate_item)[:count]))
    elif rules.use_package_dimensions and source_package and candidate_package:
        count = min(len(source_package), len(candidate_package), 3)
        if count < rules.min_dimension_count:
            return None, "not_enough_dimensions"
        pairs = list(zip(sorted(source_package)[:count], sorted(candidate_package)[:count]))
    if not pairs:
        return None, ""

    differences = [_relative_difference(first, second) for first, second in pairs]
    mean_difference = sum(differences) / len(differences)
    if mean_difference > rules.max_dimension_difference:
        return None, "dimensions_too_far"
    return (
        max(0.0, 1.0 - mean_difference / max(rules.max_dimension_difference, 1e-9)),
        "",
    )


def _dimension_values(dimensions: Mapping[str, float], package: bool) -> list[float]:
    return [
        value
        for name, value in dimensions.items()
        if ("упаков" in name) is package and value > 0
    ]


def _comparison_materials(materials: tuple[str, ...]) -> set[str]:
    return set(materials).intersection(_COMPARABLE_MATERIALS)


def _dice_similarity(first: set[str], second: set[str]) -> float:
    return 2 * len(first.intersection(second)) / (len(first) + len(second))


def _material_tokens(value: str) -> set[str]:
    normalized = value.replace("древесно-стружечная плита", "дсп")
    normalized = normalized.replace("древесностружечная плита", "дсп")
    normalized = normalized.replace("ламинированная дсп", "лдсп")
    result: set[str] = set()
    for word in _WORD_RE.findall(normalized):
        material = _MATERIAL_ALIASES.get(word)
        if material:
            result.add(material)
    return result


def _dimension_key(label: str) -> str | None:
    base = None
    if "ширин" in label:
        base = "ширина"
    elif "высот" in label:
        base = "высота"
    elif "глубин" in label:
        base = "глубина"
    elif "длин" in label:
        base = "длина"
    if base is None:
        return None
    return f"{base} упаковки" if "упаков" in label else base


def _dimension_to_cm(number: float, text: str) -> float:
    if "мм" in text:
        number /= 10
    elif re.search(r"(^|\s)м($|\s)", text) and "см" not in text and "мм" not in text:
        number *= 100
    return round(number, 2)


def _weight_to_kg(text: str) -> float | None:
    number = _first_number(text)
    if number is None or number <= 0:
        return None
    if "кг" in text:
        return round(number, 3)
    if re.search(r"(^|\s)г($|\s)", text):
        return round(number / 1000, 3)
    return round(number, 3)


def _first_number(value: str) -> float | None:
    match = _NUMBER_RE.search(value)
    return _to_float(match.group(0)) if match else None


def _to_float(value: str) -> float:
    return round(float(value.replace(",", ".")), 3)


def _relative_difference(first: float, second: float) -> float:
    return abs(first - second) / max(abs(first), abs(second), 1e-9)


def _normalize(value: str) -> str:
    return " ".join(str(value).lower().replace("ё", "е").split())
