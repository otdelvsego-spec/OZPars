"""Market comparison for Ozon products."""

from __future__ import annotations

from collections.abc import Callable
from statistics import mean, median

import requests

from .models import MarketProduct, MatchingSettings, PriceResult
from .ozon_client import OzonClient

MarketProgress = Callable[[int, int, int], None]


def _selected_result_price(result: PriceResult, basis: str) -> float | None:
    if basis == "ozon_card":
        return result.ozon_card_price or result.current_price or result.price_without_card
    if basis == "without_card":
        return result.price_without_card or result.current_price or result.ozon_card_price
    return result.current_price or result.price_without_card or result.ozon_card_price


def _selected_analogue_price(item: MarketProduct, basis: str) -> float | None:
    if basis == "ozon_card":
        return item.ozon_card_price or item.current_price or item.price_without_card
    if basis == "without_card":
        return item.price_without_card or item.current_price or item.ozon_card_price
    return item.current_price or item.price_without_card or item.ozon_card_price


def compare_with_market(
    result: PriceResult,
    client: OzonClient,
    analog_limit: int = 5,
    progress: MarketProgress | None = None,
) -> PriceResult:
    if result.status != "ok" or result.current_price is None:
        return result

    settings = getattr(client, "matching_settings", MatchingSettings())
    settings.validate()
    result.comparison_price = _selected_result_price(result, settings.price_basis)

    result = client.enrich_product_characteristics(result)
    if not result.characteristics.has_data:
        if not result.characteristics_error:
            result.market_error = "В исходной карточке нет сравнимых характеристик."
        return result

    try:
        analogues = client.search_analogues(result, limit=analog_limit, progress=progress)
    except (requests.RequestException, ValueError, RuntimeError) as error:
        result.market_error = str(error)
        return result

    if not analogues:
        if not result.market_error:
            result.market_error = "Подходящие аналоги Ozon не найдены."
        return result

    prices: list[float] = []
    selected_analogues: list[MarketProduct] = []
    for item in analogues:
        price = _selected_analogue_price(item, settings.price_basis)
        if price is None or price <= 0:
            continue
        item.comparison_price = price
        prices.append(price)
        selected_analogues.append(item)

    if not prices or result.comparison_price is None:
        result.market_error = "Не удалось определить сопоставимые цены для рыночного сравнения."
        return result

    result.analogues = selected_analogues
    market_median = float(median(prices))
    result.analog_count = len(prices)
    result.analog_min_price = min(prices)
    result.analog_median_price = round(market_median, 2)
    result.analog_average_price = round(mean(prices), 2)
    result.cheaper_analogs = sum(price < result.comparison_price for price in prices)
    result.more_expensive_analogs = sum(price > result.comparison_price for price in prices)
    result.price_vs_market = round(result.comparison_price - market_median, 2)
    if market_median > 0:
        result.price_vs_market_percent = result.price_vs_market / market_median
        if result.price_vs_market_percent < -settings.market_tolerance:
            result.market_position = "Ниже рынка"
        elif result.price_vs_market_percent > settings.market_tolerance:
            result.market_position = "Выше рынка"
        else:
            result.market_position = "На уровне рынка"
    return result
