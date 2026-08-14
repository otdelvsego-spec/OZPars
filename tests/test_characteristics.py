from ozpriceanalyzer.characteristics import extract_characteristics, similarity
from ozpriceanalyzer.models import MatchingSettings


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


def test_similarity_respects_user_overall_threshold() -> None:
    source = extract_characteristics({
        "Материал": "ЛДСП, пластик",
        "Ширина": "100 см",
        "Высота": "60 см",
        "Вес": "10 кг",
    })
    candidate = extract_characteristics({
        "Материал": "ЛДСП",
        "Ширина": "112 см",
        "Высота": "67 см",
        "Вес": "12 кг",
    })
    permissive = MatchingSettings(overall_similarity=0.60, material_similarity=0.65)
    strict = MatchingSettings(overall_similarity=0.90, material_similarity=0.65)

    permissive_score, _ = similarity(source, candidate, permissive)
    strict_score, _ = similarity(source, candidate, strict)

    assert permissive_score is not None
    assert strict_score is None


def test_similarity_can_use_package_dimensions() -> None:
    source = extract_characteristics({
        "Материал": "ЛДСП",
        "Размер упаковки": "100x50x20 см",
    })
    candidate = extract_characteristics({
        "Материал": "ЛДСП",
        "Размер упаковки": "102x49x21 см",
    })
    enabled = MatchingSettings(use_package_dimensions=True)
    disabled = MatchingSettings(use_package_dimensions=False)

    enabled_score, _ = similarity(source, candidate, enabled)
    disabled_score, _ = similarity(source, candidate, disabled)

    assert enabled_score is not None
    assert disabled_score is None
