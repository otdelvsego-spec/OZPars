"""Command-line entry point for OZPriceAnalyzer."""

from __future__ import annotations

import argparse
from pathlib import Path

from .analyzer import analyze_products
from .excel import ExcelLoader
from .history import PriceHistoryStore
from .models import Product
from .ozon_client import OzonClient
from .report import ExcelReportWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oz-analyzer", description="Анализ публичных цен Ozon без Seller API-токена.")
    parser.add_argument("--input", default="data/products.xlsx")
    parser.add_argument("--output", default="data/result.xlsx")
    parser.add_argument("--history", default="data/history.xlsx")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-market", action="store_true")
    parser.add_argument("--analogs", type=int, default=5)
    parser.add_argument("--candidate-checks", type=int, default=20)
    parser.add_argument("--market-time-limit", type=float, default=60.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=2)
    return parser


def run(
    input_file: str | Path = "data/products.xlsx",
    output_file: str | Path = "data/result.xlsx",
    history_file: str | Path = "data/history.xlsx",
    history_enabled: bool = True,
    market_enabled: bool = True,
    analog_limit: int = 5,
    candidate_check_limit: int = 20,
    market_time_limit: float = 60.0,
    timeout: float = 15.0,
    workers: int = 2,
) -> int:
    products = ExcelLoader(input_file).load()
    if not products:
        raise ValueError("Во входном файле нет артикулов Ozon.")
    if workers < 1:
        raise ValueError("Количество потоков должно быть не меньше 1.")
    if analog_limit < 1 or analog_limit > 30:
        raise ValueError("Количество аналогов должно быть от 1 до 30.")

    print(f"Загружено товаров: {len(products)}")
    print(f"Параллельно обрабатывается: {min(workers, len(products))}")
    if market_enabled:
        print(
            "Аналоги: ЛДСП/пластик ≥65%, итоговое сходство ≥65%; "
            f"до {analog_limit} результатов, проверка до {max(candidate_check_limit, analog_limit * 4)} карточек."
        )

    client = OzonClient(
        timeout=timeout,
        market_time_limit=market_time_limit,
        candidate_check_limit=candidate_check_limit,
    )

    def progress(completed: int, total: int, product: Product) -> None:
        print(f"[{completed}/{total}] Обработан артикул {product.sku}", flush=True)

    def stage(product: Product, message: str) -> None:
        print(f"[{product.sku}] {message}", flush=True)

    results = analyze_products(
        products,
        client,
        progress=progress,
        workers=workers,
        market_enabled=market_enabled,
        analog_limit=analog_limit,
        stage_progress=stage,
    )

    history_path = None
    if history_enabled:
        history_path = PriceHistoryStore(history_file).update(results)
    report_path = ExcelReportWriter(output_file).write(results)

    successful = sum(item.status == "ok" for item in results)
    failed = sum(item.status == "error" for item in results)
    compared = sum(item.analog_count > 0 for item in results)
    print(f"Готово: успешно — {successful}, ошибок — {failed}, сравнение рынка — {compared}.")
    print(f"Отчёт сохранён: {report_path.resolve()}")
    if history_path:
        print(f"История обновлена: {history_path.resolve()}")
    return 0 if successful else 1


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = run(
            input_file=args.input,
            output_file=args.output,
            history_file=args.history,
            history_enabled=not args.no_history,
            market_enabled=not args.no_market,
            analog_limit=args.analogs,
            candidate_check_limit=args.candidate_checks,
            market_time_limit=args.market_time_limit,
            timeout=args.timeout,
            workers=args.workers,
        )
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        code = 130
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Ошибка: {error}")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
