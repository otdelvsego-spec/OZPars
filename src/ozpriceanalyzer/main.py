"""Command-line entry point for OZPriceAnalyzer."""

from __future__ import annotations

import argparse
from pathlib import Path

from .analyzer import analyze_products
from .configured_client import ConfiguredOzonClient
from .excel import ExcelLoader
from .exporter import export_results
from .models import MatchingSettings, Product


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oz-analyzer",
        description="Анализ публичных цен и аналогов Ozon без Seller API-токена.",
    )
    parser.add_argument("--input", default="data/products.xlsx")
    parser.add_argument("--output", default="exports/OZ_Аналоги.xlsx")
    parser.add_argument("--no-market", action="store_true")
    parser.add_argument("--analogs", type=int, default=5)
    parser.add_argument("--candidate-checks", type=int, default=20)
    parser.add_argument("--market-time-limit", type=float, default=60.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--similarity", type=float, default=65.0)
    return parser


def run(
    input_file: str | Path = "data/products.xlsx",
    output_file: str | Path = "exports/OZ_Аналоги.xlsx",
    market_enabled: bool = True,
    analog_limit: int = 5,
    candidate_check_limit: int = 20,
    market_time_limit: float = 60.0,
    timeout: float = 15.0,
    workers: int = 2,
    similarity_percent: float = 65.0,
) -> int:
    products = ExcelLoader(input_file).load()
    if not products:
        raise ValueError("Во входном файле нет артикулов Ozon.")
    if workers < 1:
        raise ValueError("Количество потоков должно быть не меньше 1.")
    if analog_limit < 1 or analog_limit > 10:
        raise ValueError("Количество аналогов должно быть от 1 до 10.")
    if candidate_check_limit < 1 or candidate_check_limit > 30:
        raise ValueError("Количество проверяемых кандидатов должно быть от 1 до 30.")
    if not 0 <= similarity_percent <= 100:
        raise ValueError("Сходство должно быть от 0 до 100%.")

    rules = MatchingSettings(overall_similarity=similarity_percent / 100)
    client = ConfiguredOzonClient(
        matching_settings=rules,
        timeout=timeout,
        market_time_limit=market_time_limit,
        candidate_check_limit=candidate_check_limit,
    )

    print(f"Загружено товаров: {len(products)}")
    print(f"Параллельно обрабатывается: {min(workers, len(products))}")
    if market_enabled:
        print(
            f"Аналоги: итоговое сходство ≥{rules.overall_similarity:.0%}; "
            f"до {analog_limit} результатов, проверка до "
            f"{min(max(candidate_check_limit, analog_limit * 4), 30)} карточек."
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
    report_path = export_results(results, output_file, analogue_limit=analog_limit)

    successful = sum(item.status == "ok" for item in results)
    failed = sum(item.status == "error" for item in results)
    compared = sum(item.analog_count > 0 for item in results)
    print(f"Готово: успешно — {successful}, ошибок — {failed}, сравнение рынка — {compared}.")
    print(f"Экспорт сохранён: {report_path.resolve()}")
    return 0 if successful else 1


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = run(
            input_file=args.input,
            output_file=args.output,
            market_enabled=not args.no_market,
            analog_limit=args.analogs,
            candidate_check_limit=args.candidate_checks,
            market_time_limit=args.market_time_limit,
            timeout=args.timeout,
            workers=args.workers,
            similarity_percent=args.similarity,
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
