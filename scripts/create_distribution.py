from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parents[1]
DIST_EXE = ROOT / "dist" / "OZPriceAnalyzer.exe"
APP_DIR = ROOT / "release" / "OZPriceAnalyzer"
DATA_DIR = APP_DIR / "data"


def main() -> None:
    if not DIST_EXE.is_file():
        raise FileNotFoundError(DIST_EXE)
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DIST_EXE, APP_DIR / "OZPriceAnalyzer.exe")
    shutil.copy2(ROOT / "DISTRIBUTION_README.txt", APP_DIR / "README.txt")
    pd.DataFrame(columns=["Артикул Ozon", "Название", "Штрихкод", "Целевая цена"]).to_excel(
        DATA_DIR / "products.xlsx", index=False
    )
    (APP_DIR / "VERSION.txt").write_text(VERSION + "\n", encoding="utf-8")
    print(f"Distribution created: {APP_DIR}")


if __name__ == "__main__":
    main()
