from ozpriceanalyzer.ozon_client import OzonClient


PRODUCT_HTML = '''
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"Стол журнальный","brand":{"name":"Brand"},
 "offers":{"@type":"Offer","price":"14087"},
 "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.8","reviewCount":"123"}}
</script>
<script type="application/ld+json">
{"@type":"BreadcrumbList","itemListElement":[
 {"@type":"ListItem","position":1,"name":"Дом и сад"},
 {"@type":"ListItem","position":2,"name":"Журнальные столы"}]}
</script></head>
<body><div>12 678 ₽ с Ozon Картой</div><div>14 087 ₽ без Ozon Карты</div></body></html>
'''

FEATURES_HTML = '''
<html><body><h1>Характеристики</h1><dl>
<dt>Материал корпуса</dt><dd>ЛДСП, пластик</dd>
<dt>Размер упаковки (Длина х Ширина х Высота), см</dt><dd>54х50х18</dd>
<dt>Вес товара, кг</dt><dd>12,5 кг</dd>
</dl></body></html>
'''


def test_parse_product_page() -> None:
    parsed = OzonClient._parse_product_page(
        "3362949093",
        "https://www.ozon.ru/product/test-3362949093/",
        PRODUCT_HTML,
    )
    assert parsed["name"] == "Стол журнальный"
    assert parsed["brand"] == "Brand"
    assert parsed["category"] == "Журнальные столы"
    assert parsed["ozon_card_price"] == 12678
    assert parsed["price_without_card"] == 14087
    assert parsed["current_price"] == 12678
    assert parsed["rating"] == 4.8
    assert parsed["feedbacks"] == 123


def test_parse_features_page() -> None:
    raw = OzonClient._parse_features_page(FEATURES_HTML)
    assert raw["Материал корпуса"] == "ЛДСП, пластик"
    assert raw["Размер упаковки (Длина х Ширина х Высота), см"] == "54х50х18"
    assert raw["Вес товара, кг"] == "12,5 кг"


def test_product_href_pattern_supports_slug_and_plain_sku() -> None:
    assert OzonClient._url_contains_sku(
        "https://www.ozon.ru/product/stol-3362949093/", "3362949093"
    )
    assert OzonClient._url_contains_sku(
        "https://www.ozon.ru/product/3362949093/", "3362949093"
    )
