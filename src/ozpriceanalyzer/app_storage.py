"""Persistent local application storage backed by SQLite."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import MarketProduct, MatchingSettings, PriceResult, Product


@dataclass(slots=True)
class CatalogEntry:
    sku: str
    name: str
    barcode: str
    target_price: float | None
    updated_at: str


@dataclass(slots=True)
class ExclusionEntry:
    sku: str
    note: str
    created_at: str


@dataclass(slots=True)
class RunSummary:
    id: int
    name: str
    created_at: str
    source_file: str
    item_count: int
    analogue_count: int
    below_count: int
    at_count: int
    above_count: int
    error_count: int


class AppStorage:
    """Keep catalog, exclusions, settings and report history in one database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS products (
                    sku TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    barcode TEXT NOT NULL DEFAULT '',
                    target_price REAL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS exclusions (
                    sku TEXT PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_file TEXT NOT NULL DEFAULT '',
                    settings_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS run_items (
                    run_id INTEGER NOT NULL,
                    sku TEXT NOT NULL,
                    input_name TEXT NOT NULL DEFAULT '',
                    ozon_name TEXT NOT NULL DEFAULT '',
                    current_price REAL,
                    comparison_price REAL,
                    market_median_price REAL,
                    market_position TEXT NOT NULL DEFAULT '',
                    price_vs_market_percent REAL,
                    url TEXT NOT NULL DEFAULT '',
                    market_error TEXT NOT NULL DEFAULT '',
                    analog_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (run_id, sku),
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_analogues (
                    run_id INTEGER NOT NULL,
                    source_sku TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    sku TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    current_price REAL,
                    comparison_price REAL,
                    similarity_score REAL,
                    rating REAL,
                    feedbacks INTEGER,
                    url TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (run_id, source_sku, position),
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                """
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )

    def set_settings(self, values: dict[str, str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                [(key, str(value)) for key, value in values.items()],
            )

    def matching_settings(self) -> MatchingSettings:
        def number(key: str, default: float) -> float:
            try:
                return float(self.get_setting(key, str(default)))
            except ValueError:
                return default

        def integer(key: str, default: int) -> int:
            try:
                return int(float(self.get_setting(key, str(default))))
            except ValueError:
                return default

        settings = MatchingSettings(
            overall_similarity=number("overall_similarity", 0.65),
            material_similarity=number("material_similarity", 0.65),
            max_dimension_difference=number("max_dimension_difference", 0.60),
            max_weight_difference=number("max_weight_difference", 0.80),
            material_weight=number("material_weight", 0.40),
            dimensions_weight=number("dimensions_weight", 0.45),
            weight_weight=number("weight_weight", 0.15),
            strict_category=self.get_setting("strict_category", "1") == "1",
            min_rating=number("min_rating", 0.0),
            min_feedbacks=integer("min_feedbacks", 0),
            min_dimension_count=integer("min_dimension_count", 2),
            use_package_dimensions=self.get_setting("use_package_dimensions", "1") == "1",
            price_basis=self.get_setting("price_basis", "current"),
            market_tolerance=number("market_tolerance", 0.05),
        )
        settings.validate()
        return settings

    def upsert_products(self, products: Iterable[Product]) -> int:
        items = list(products)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            for product in items:
                connection.execute(
                    """
                    INSERT INTO products(sku, name, barcode, target_price, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(sku) DO UPDATE SET
                        name = excluded.name,
                        barcode = excluded.barcode,
                        target_price = excluded.target_price,
                        updated_at = excluded.updated_at
                    """,
                    (
                        product.sku,
                        product.name,
                        product.barcode,
                        product.target_price,
                        timestamp,
                    ),
                )
        return len(items)

    def save_product(self, product: Product) -> None:
        self.upsert_products([product])

    def delete_product(self, sku: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM products WHERE sku = ?", (sku,))

    def clear_products(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM products")

    def list_catalog(self) -> list[CatalogEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sku, name, barcode, target_price, updated_at
                FROM products
                ORDER BY CASE WHEN name = '' THEN 1 ELSE 0 END, name COLLATE NOCASE, sku
                """
            ).fetchall()
        return [
            CatalogEntry(
                sku=str(row["sku"]),
                name=str(row["name"]),
                barcode=str(row["barcode"]),
                target_price=float(row["target_price"]) if row["target_price"] is not None else None,
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def catalog_products(self) -> list[Product]:
        return [
            Product(
                sku=item.sku,
                name=item.name,
                barcode=item.barcode,
                target_price=item.target_price,
            )
            for item in self.list_catalog()
        ]

    def add_exclusion(self, sku: str, note: str = "") -> None:
        sku = str(sku).strip()
        if not sku or not sku.isdigit():
            raise ValueError("Артикул исключения Ozon должен состоять из цифр.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO exclusions(sku, note, created_at) VALUES(?, ?, ?)
                ON CONFLICT(sku) DO UPDATE SET note = excluded.note
                """,
                (sku, note.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

    def remove_exclusion(self, sku: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM exclusions WHERE sku = ?", (str(sku),))

    def clear_exclusions(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM exclusions")

    def list_exclusions(self) -> list[ExclusionEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sku, note, created_at FROM exclusions ORDER BY created_at DESC, sku"
            ).fetchall()
        return [
            ExclusionEntry(str(row["sku"]), str(row["note"]), str(row["created_at"]))
            for row in rows
        ]

    def exclusion_skus(self) -> set[str]:
        return {item.sku for item in self.list_exclusions()}

    def save_run(
        self,
        results: list[PriceResult],
        *,
        source_file: str = "",
        settings: MatchingSettings | None = None,
        name: str | None = None,
    ) -> int:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_name = name or f"Отчет {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        settings_json = json.dumps(
            asdict(settings or MatchingSettings()),
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs(name, created_at, source_file, settings_json) VALUES(?, ?, ?, ?)",
                (report_name, timestamp, source_file, settings_json),
            )
            run_id = int(cursor.lastrowid)
            for result in results:
                connection.execute(
                    """
                    INSERT INTO run_items(
                        run_id, sku, input_name, ozon_name, current_price, comparison_price,
                        market_median_price, market_position, price_vs_market_percent, url,
                        market_error, analog_count, status, error
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        result.sku,
                        result.input_name,
                        result.ozon_name,
                        result.current_price,
                        result.comparison_price or result.current_price,
                        result.analog_median_price,
                        result.market_position,
                        result.price_vs_market_percent,
                        result.url,
                        result.market_error,
                        len(result.analogues),
                        result.status,
                        result.error,
                    ),
                )
                for position, analogue in enumerate(result.analogues, start=1):
                    connection.execute(
                        """
                        INSERT INTO run_analogues(
                            run_id, source_sku, position, sku, name, current_price,
                            comparison_price, similarity_score, rating, feedbacks, url
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            result.sku,
                            position,
                            analogue.sku,
                            analogue.name,
                            analogue.current_price,
                            analogue.comparison_price or analogue.current_price,
                            analogue.similarity_score,
                            analogue.rating,
                            analogue.feedbacks,
                            analogue.url,
                        ),
                    )
        return run_id

    def list_runs(self) -> list[RunSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.id, r.name, r.created_at, r.source_file,
                    COUNT(i.sku) AS item_count,
                    COALESCE(SUM(i.analog_count), 0) AS analogue_count,
                    COALESCE(SUM(CASE WHEN i.market_position = 'Ниже рынка' THEN 1 ELSE 0 END), 0) AS below_count,
                    COALESCE(SUM(CASE WHEN i.market_position = 'На уровне рынка' THEN 1 ELSE 0 END), 0) AS at_count,
                    COALESCE(SUM(CASE WHEN i.market_position = 'Выше рынка' THEN 1 ELSE 0 END), 0) AS above_count,
                    COALESCE(SUM(CASE WHEN i.status = 'error' THEN 1 ELSE 0 END), 0) AS error_count
                FROM runs r
                LEFT JOIN run_items i ON i.run_id = r.id
                GROUP BY r.id
                ORDER BY r.created_at ASC, r.id ASC
                """
            ).fetchall()
        return [
            RunSummary(
                id=int(row["id"]),
                name=str(row["name"]),
                created_at=str(row["created_at"]),
                source_file=str(row["source_file"]),
                item_count=int(row["item_count"] or 0),
                analogue_count=int(row["analogue_count"] or 0),
                below_count=int(row["below_count"] or 0),
                at_count=int(row["at_count"] or 0),
                above_count=int(row["above_count"] or 0),
                error_count=int(row["error_count"] or 0),
            )
            for row in rows
        ]

    def run_settings(self, run_id: int) -> MatchingSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT settings_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Отчет {run_id} не найден.")
        try:
            payload = json.loads(str(row["settings_json"]))
            settings = MatchingSettings(**payload)
            settings.validate()
            return settings
        except (TypeError, ValueError, json.JSONDecodeError):
            return MatchingSettings()

    def load_run(self, run_id: int) -> list[PriceResult]:
        with self._connect() as connection:
            item_rows = connection.execute(
                "SELECT * FROM run_items WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
            analogue_rows = connection.execute(
                """
                SELECT * FROM run_analogues
                WHERE run_id = ?
                ORDER BY source_sku, position
                """,
                (run_id,),
            ).fetchall()

        analogues_by_source: dict[str, list[MarketProduct]] = {}
        for row in analogue_rows:
            analogue = MarketProduct(
                sku=str(row["sku"]),
                name=str(row["name"]),
                brand="",
                category_name="",
                current_price=float(row["current_price"] or row["comparison_price"] or 0),
                rating=float(row["rating"]) if row["rating"] is not None else None,
                feedbacks=int(row["feedbacks"]) if row["feedbacks"] is not None else None,
                similarity_score=(
                    float(row["similarity_score"])
                    if row["similarity_score"] is not None
                    else None
                ),
                url=str(row["url"]),
                comparison_price=(
                    float(row["comparison_price"])
                    if row["comparison_price"] is not None
                    else None
                ),
            )
            analogues_by_source.setdefault(str(row["source_sku"]), []).append(analogue)

        results: list[PriceResult] = []
        for row in item_rows:
            sku = str(row["sku"])
            analogues = analogues_by_source.get(sku, [])
            results.append(
                PriceResult(
                    sku=sku,
                    input_name=str(row["input_name"]),
                    ozon_name=str(row["ozon_name"]),
                    current_price=(
                        float(row["current_price"]) if row["current_price"] is not None else None
                    ),
                    comparison_price=(
                        float(row["comparison_price"])
                        if row["comparison_price"] is not None
                        else None
                    ),
                    analogues=analogues,
                    analog_count=len(analogues),
                    analog_median_price=(
                        float(row["market_median_price"])
                        if row["market_median_price"] is not None
                        else None
                    ),
                    market_position=str(row["market_position"]),
                    price_vs_market_percent=(
                        float(row["price_vs_market_percent"])
                        if row["price_vs_market_percent"] is not None
                        else None
                    ),
                    market_error=str(row["market_error"]),
                    url=str(row["url"]),
                    status=str(row["status"]),
                    error=str(row["error"]),
                )
            )
        return results

    def rename_run(self, run_id: int, name: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Название отчета не может быть пустым.")
        with self._connect() as connection:
            connection.execute("UPDATE runs SET name = ? WHERE id = ?", (cleaned, run_id))

    def delete_run(self, run_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def run_name(self, run_id: int) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT name FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Отчет {run_id} не найден.")
        return str(row["name"])
