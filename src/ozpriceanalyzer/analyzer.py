"""Product analysis orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

import requests

from .market import compare_with_market
from .models import PriceResult, Product
from .ozon_client import OzonClient

ProgressCallback = Callable[[int, int, Product], None]
StageProgressCallback = Callable[[Product, str], None]


def _apply_target(product: Product, result: PriceResult) -> PriceResult:
    result.target_price = product.target_price
    if result.status == "ok" and result.current_price is not None and product.target_price is not None:
        result.target_difference = round(result.current_price - product.target_price, 2)
        result.target_reached = result.current_price <= product.target_price
    return result


def _analyze_one(
    product: Product,
    client: OzonClient,
    market_enabled: bool,
    analog_limit: int,
    stage_progress: StageProgressCallback | None,
) -> PriceResult:
    if stage_progress:
        stage_progress(product, "получение карточки и цены Ozon")
    try:
        result = client.get_product(product)
    except (requests.RequestException, ValueError, RuntimeError) as error:
        result = PriceResult(
            sku=product.sku,
            input_name=product.name,
            barcode=product.barcode,
            url=f"https://www.ozon.ru/product/{product.sku}/",
            status="error",
            error=str(error),
        )
    result = _apply_target(product, result)

    if market_enabled and result.status == "ok":
        if stage_progress:
            stage_progress(product, "загрузка характеристик и поиск аналогов")

        def market_progress(checked: int, total: int, found: int) -> None:
            if stage_progress and (checked == 1 or checked == total or checked % 3 == 0):
                stage_progress(
                    product,
                    f"аналоги: проверено {checked}/{total}, найдено {found}",
                )

        result = compare_with_market(
            result,
            client,
            analog_limit=analog_limit,
            progress=market_progress,
        )
    return result


def analyze_products(
    products: Iterable[Product],
    client: OzonClient,
    progress: ProgressCallback | None = None,
    workers: int = 1,
    market_enabled: bool = True,
    analog_limit: int = 5,
    stage_progress: StageProgressCallback | None = None,
) -> list[PriceResult]:
    if workers < 1:
        raise ValueError("Количество потоков должно быть не меньше 1.")
    product_list = list(products)
    if not product_list:
        return []

    ordered: list[PriceResult | None] = [None] * len(product_list)
    executor = ThreadPoolExecutor(max_workers=min(workers, len(product_list)), thread_name_prefix="oz-price")
    future_map: dict[Future[PriceResult], tuple[int, Product]] = {}
    try:
        future_map = {
            executor.submit(
                _analyze_one,
                product,
                client,
                market_enabled,
                analog_limit,
                stage_progress,
            ): (index, product)
            for index, product in enumerate(product_list)
        }
        completed = 0
        for future in as_completed(future_map):
            index, product = future_map[future]
            ordered[index] = future.result()
            completed += 1
            if progress:
                progress(completed, len(product_list), product)
    except KeyboardInterrupt:
        for future in future_map:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return [item for item in ordered if item is not None]
