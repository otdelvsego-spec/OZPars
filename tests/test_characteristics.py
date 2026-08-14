from ozpriceanalyzer.characteristics import extract_characteristics, similarity


def test_extract_ozon_characteristics() -> None:
    raw = {
        "Материал корпуса": "ЛДСП, пластик, металл",
        "Размер упаковки (Длина х Ширина х Высота), см": "54х50х18",
        "Вес товара, г": "12500 г",
    }
    item = extract_characteristics(raw)
    assert item.materials == ("лдсп", "пластик")
    assert item.dimensions_cm["длина упаковки"] == 54
    assert item.dimensions_cm["ширина упаковки"] == 50
    assert item.dimensions_cm["высота упаковки"] == 18
    assert item.weight_kg == 12.5


def test_similarity_accepts_67_percent_material_match() -> None:
    source = extract_characteristics({
        "Материал": "ЛДСП, пластик",
        "Ширина, см": "100 см",
        "Высота, см": "50 см",
    })
    candidate = extract_characteristics({
        "Материал": "ЛДСП",
        "Ширина, см": "102 см",
        "Высота, см": "49 см",
    })
    score, fields = similarity(source, candidate)
    assert score is not None
    assert score >= 0.65
    assert "материалы 67%" in fields


def test_similarity_rejects_different_material() -> None:
    source = extract_characteristics({"Материал": "ЛДСП", "Ширина": "100 см"})
    candidate = extract_characteristics({"Материал": "пластик", "Ширина": "100 см"})
    score, fields = similarity(source, candidate)
    assert score is None
    assert fields == ()
