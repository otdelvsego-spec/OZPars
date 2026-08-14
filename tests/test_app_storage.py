from ozpriceanalyzer.app_storage import AppStorage
from ozpriceanalyzer.models import MarketProduct, MatchingSettings, PriceResult, Product


def test_storage_keeps_catalog_exclusions_and_report_history(tmp_path) -> None:
    storage = AppStorage(tmp_path / "app.db")
    storage.upsert_products([
        Product("100", "Стол", "4600000000001", 1999.0),
        Product("200", "Тумба"),
    ])
    assert [item.sku for item in storage.list_catalog()] == ["100", "200"]

    storage.add_exclusion("999", "Не считать аналогом")
    assert storage.exclusion_skus() == {"999"}

    analogue = MarketProduct(
        sku="300",
        name="Стол аналог",
        brand="Brand",
        category_name="Столы",
        current_price=1800.0,
        comparison_price=1850.0,
        similarity_score=0.84,
        rating=4.8,
        feedbacks=120,
        url="https://www.ozon.ru/product/300/",
    )
    result = PriceResult(
        sku="100",
        input_name="Стол",
        ozon_name="Стол Ozon",
        current_price=1900.0,
        comparison_price=1950.0,
        analogues=[analogue],
        analog_count=1,
        analog_median_price=1850.0,
        market_position="Выше рынка",
        price_vs_market_percent=0.054,
        url="https://www.ozon.ru/product/100/",
    )
    run_id = storage.save_run(
        [result],
        source_file="products.xlsx",
        settings=MatchingSettings(overall_similarity=0.70),
        name="Проверка",
    )

    runs = storage.list_runs()
    assert len(runs) == 1
    assert runs[0].id == run_id
    assert runs[0].item_count == 1
    assert runs[0].analogue_count == 1
    assert runs[0].above_count == 1

    loaded = storage.load_run(run_id)
    assert loaded[0].comparison_price == 1950.0
    assert loaded[0].analogues[0].sku == "300"
    assert loaded[0].analogues[0].comparison_price == 1850.0
    assert storage.run_settings(run_id).overall_similarity == 0.70

    storage.rename_run(run_id, "Новое имя")
    assert storage.run_name(run_id) == "Новое имя"
    storage.delete_run(run_id)
    assert storage.list_runs() == []
