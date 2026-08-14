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
