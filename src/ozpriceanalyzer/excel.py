"""Loading and validating products from Excel."""

from __future__ import annotations

from math import isfinite
from pathlib import Path

import pandas as pd

from .models import Product


class ExcelLoader:
    SKU_ALIASES = ("Артикул Ozon", "Артикул OZON", "Артикул", "SKU", "Ozon ID")

    def __init__(self, filename: str | Path) -> None:
        self.path = Path(filename)

    def load(self) -> list[Product]:
        if not self.path.is_file():
            raise FileNotFoundError(f"Excel-файл не найден: {self.path}")
        dataframe = pd.read_excel(self.path, dtype=object)
        dataframe.columns = [str(column).strip() for column in dataframe.columns]
        sku_column = next((name for name in self.SKU_ALIASES if name in dataframe.columns), None)
        if sku_column is None:
            raise ValueError(
                "В Excel отсутствует колонка с артикулом Ozon. "
                "Используйте «Артикул Ozon» или «Артикул»."
            )

        products: list[Product] = []
        for excel_row, (_, row) in enumerate(dataframe.iterrows(), start=2):
            sku = self._cell_to_text(row.get(sku_column))
            if not sku:
                continue
            if not sku.isdigit():
                raise ValueError(f"Некорректный артикул Ozon в строке {excel_row}: {sku!r}.")
            products.append(
                Product(
                    sku=sku,
                    name=self._cell_to_text(row.get("Название")),
                    barcode=self._cell_to_text(row.get("Штрихкод")),
                    target_price=self._positive_price(row.get("Целевая цена"), excel_row),
                )
            )
        return products

    @staticmethod
    def _cell_to_text(value: object) -> str:
        if value is None or pd.isna(value):
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    @staticmethod
    def _positive_price(value: object, excel_row: int) -> float | None:
        if value is None or pd.isna(value):
            return None
        normalized = value
        if isinstance(value, str):
            normalized = (
                value.strip().replace("\u00a0", "").replace(" ", "")
                .replace("₽", "").replace(",", ".")
            )
        try:
            number = float(normalized)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Некорректная целевая цена в строке {excel_row}: {value!r}."
            ) from error
        if not isfinite(number) or number <= 0:
            raise ValueError(f"Целевая цена в строке {excel_row} должна быть больше нуля.")
        return round(number, 2)
