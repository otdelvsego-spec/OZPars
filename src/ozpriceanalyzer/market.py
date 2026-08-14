"""Market comparison for Ozon products."""

from __future__ import annotations

from collections.abc import Callable
from statistics import mean, median

import requests

from .models import PriceResult
from .ozon_client import OzonClient

MARKET_TOLERANCE = 0.05
MarketProgress = Callable[[int, int, int], None]


def compare_with_market(
    result: PriceResult,
    client: OzonClient,
    analog_limit: int = 5,
    progress: MarketProgress | None = None,
) -> PriceResult:
    if result.status != "ok" or result.current_price is None:
        return result

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

    result.analogues = analogues
    prices = [item.current_price for item in analogues]
    market_median = float(median(prices))
    result.analog_count = len(prices)
    result.analog_min_price = min(prices)
    result.analog_median_price = round(market_median, 2)
    result.analog_average_price = round(mean(prices), 2)
    result.cheaper_analogs = sum(price < result.current_price for price in prices)
    result.more_expensive_analogs = sum(price > result.current_price for price in prices)
    result.price_vs_market = round(result.current_price - market_median, 2)
    if market_median > 0:
        result.price_vs_market_percent = result.price_vs_market / market_median
        if result.price_vs_market_percent < -MARKET_TOLERANCE:
            result.market_position = "Ниже рынка"
        elif result.price_vs_market_percent > MARKET_TOLERANCE:
            result.market_position = "Выше рынка"
        else:
            result.market_position = "На уровне рынка"
    return result
