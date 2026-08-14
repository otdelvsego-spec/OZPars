"""Project data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Product:
    sku: str
    name: str = ""
    barcode: str = ""
    target_price: float | None = None


@dataclass(slots=True)
class MatchingSettings:
    """User-configurable analogue matching rules."""

    overall_similarity: float = 0.65
    material_similarity: float = 0.65
    max_dimension_difference: float = 0.60
    max_weight_difference: float = 0.80
    material_weight: float = 0.40
    dimensions_weight: float = 0.45
    weight_weight: float = 0.15
    strict_category: bool = True
    min_rating: float = 0.0
    min_feedbacks: int = 0
    min_dimension_count: int = 2
    use_package_dimensions: bool = True
    price_basis: str = "current"
    market_tolerance: float = 0.05

    def validate(self) -> None:
        for label, value in (
            ("Общее сходство", self.overall_similarity),
            ("Сходство материалов", self.material_similarity),
            ("Отклонение габаритов", self.max_dimension_difference),
            ("Отклонение веса", self.max_weight_difference),
            ("Граница рынка", self.market_tolerance),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{label} должно быть от 0 до 100%.")
        if self.material_weight < 0 or self.dimensions_weight < 0 or self.weight_weight < 0:
            raise ValueError("Вес параметров сходства не может быть отрицательным.")
        if self.material_weight + self.dimensions_weight + self.weight_weight <= 0:
            raise ValueError("Хотя бы один вес параметра сходства должен быть больше нуля.")
        if self.min_rating < 0 or self.min_rating > 5:
            raise ValueError("Минимальный рейтинг должен быть от 0 до 5.")
        if self.min_feedbacks < 0:
            raise ValueError("Минимальное число отзывов не может быть отрицательным.")
        if self.min_dimension_count < 1 or self.min_dimension_count > 3:
            raise ValueError("Число сопоставимых габаритов должно быть от 1 до 3.")
        if self.price_basis not in {"current", "without_card", "ozon_card"}:
            raise ValueError("Неизвестный тип цены для рыночного сравнения.")


@dataclass(slots=True)
class ProductCharacteristics:
    dimensions_cm: dict[str, float] = field(default_factory=dict)
    weight_kg: float | None = None
    materials: tuple[str, ...] = ()
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return bool(self.dimensions_cm or self.weight_kg is not None or self.materials)


@dataclass(slots=True)
class MarketProduct:
    sku: str
    name: str
    brand: str
    category_name: str
    current_price: float
    ozon_card_price: float | None = None
    price_without_card: float | None = None
    old_price: float | None = None
    rating: float | None = None
    feedbacks: int | None = None
    characteristics: ProductCharacteristics = field(default_factory=ProductCharacteristics)
    similarity_score: float | None = None
    matched_fields: tuple[str, ...] = ()
    url: str = ""
    comparison_price: float | None = None


@dataclass(slots=True)
class PriceResult:
    sku: str
    input_name: str = ""
    barcode: str = ""
    ozon_name: str = ""
    brand: str = ""
    category_name: str = ""
    current_price: float | None = None
    ozon_card_price: float | None = None
    price_without_card: float | None = None
    old_price: float | None = None
    comparison_price: float | None = None
    target_price: float | None = None
    target_difference: float | None = None
    target_reached: bool | None = None
    previous_price: float | None = None
    price_change: float | None = None
    price_change_percent: float | None = None
    characteristics: ProductCharacteristics = field(default_factory=ProductCharacteristics)
    characteristics_error: str = ""
    analogues: list[MarketProduct] = field(default_factory=list)
    analog_count: int = 0
    analog_checked: int = 0
    analog_diagnostics: str = ""
    analog_min_price: float | None = None
    analog_median_price: float | None = None
    analog_average_price: float | None = None
    cheaper_analogs: int = 0
    more_expensive_analogs: int = 0
    price_vs_market: float | None = None
    price_vs_market_percent: float | None = None
    market_position: str = ""
    market_error: str = ""
    rating: float | None = None
    feedbacks: int | None = None
    checked_at: str = ""
    url: str = ""
    status: str = "ok"
    error: str = ""
