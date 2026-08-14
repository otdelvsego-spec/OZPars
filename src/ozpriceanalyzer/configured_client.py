"""Configurable analogue search built on top of the public Ozon client."""

from __future__ import annotations

from collections import Counter
from time import monotonic

import requests

from .characteristics import evaluate_similarity, rejection_labels
from .models import MarketProduct, MatchingSettings, PriceResult, Product
from .ozon_client import AnalogueProgress, OzonClient


class ConfiguredOzonClient(OzonClient):
    """Ozon client that applies persisted user matching settings and exclusions."""

    def __init__(
        self,
        *,
        matching_settings: MatchingSettings | None = None,
        excluded_skus: set[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.matching_settings = matching_settings or MatchingSettings()
        self.matching_settings.validate()
        self.excluded_skus = {str(sku).strip() for sku in (excluded_skus or set()) if str(sku).strip()}

    def search_analogues(
        self,
        result: PriceResult,
        limit: int = 5,
        progress: AnalogueProgress | None = None,
    ) -> list[MarketProduct]:
        if limit < 1 or limit > 30:
            raise ValueError("Количество аналогов должно быть от 1 до 30.")
        if not result.characteristics.has_data:
            raise ValueError("Исходная карточка не содержит сравнимых характеристик.")

        rules = self.matching_settings
        deadline = monotonic() + self.market_time_limit
        check_limit = min(max(limit * 4, self.candidate_check_limit), 30)
        pool_limit = min(max(check_limit * 3, 30), 90)
        candidates_skus = self._search_candidate_skus(result, pool_limit)
        diagnostics: Counter[str] = Counter()
        analogues: list[MarketProduct] = []
        checked = 0

        for sku in candidates_skus:
            if checked >= check_limit or monotonic() >= deadline:
                break
            if sku == result.sku:
                continue
            if sku in self.excluded_skus:
                diagnostics["excluded"] += 1
                continue

            checked += 1
            try:
                candidate_result = self.get_product(Product(sku=sku))
                candidate_result = self.enrich_product_characteristics(candidate_result)
                if candidate_result.current_price is None:
                    diagnostics["missing_price"] += 1
                    continue
                if candidate_result.characteristics_error and not candidate_result.characteristics.has_data:
                    diagnostics["card_error"] += 1
                    continue
                if (
                    rules.strict_category
                    and result.category_name
                    and candidate_result.category_name
                    and self._normalize_category(result.category_name)
                    != self._normalize_category(candidate_result.category_name)
                ):
                    diagnostics["category_mismatch"] += 1
                    continue
                if rules.min_rating > 0 and (candidate_result.rating or 0) < rules.min_rating:
                    diagnostics["rating_below_threshold"] += 1
                    continue
                if rules.min_feedbacks > 0 and (candidate_result.feedbacks or 0) < rules.min_feedbacks:
                    diagnostics["feedbacks_below_threshold"] += 1
                    continue

                score, matched_fields, reason = evaluate_similarity(
                    result.characteristics,
                    candidate_result.characteristics,
                    rules,
                )
                if score is None:
                    diagnostics[reason or "total_below_threshold"] += 1
                    continue

                analogues.append(
                    MarketProduct(
                        sku=sku,
                        name=candidate_result.ozon_name,
                        brand=candidate_result.brand,
                        category_name=candidate_result.category_name,
                        current_price=candidate_result.current_price,
                        ozon_card_price=candidate_result.ozon_card_price,
                        price_without_card=candidate_result.price_without_card,
                        old_price=candidate_result.old_price,
                        rating=candidate_result.rating,
                        feedbacks=candidate_result.feedbacks,
                        characteristics=candidate_result.characteristics,
                        similarity_score=score,
                        matched_fields=matched_fields,
                        url=candidate_result.url,
                    )
                )
            except (requests.RequestException, ValueError, RuntimeError):
                diagnostics["card_error"] += 1

            if progress is not None:
                progress(checked, min(check_limit, len(candidates_skus)), len(analogues))
            if len(analogues) >= limit:
                break

        result.analog_checked = checked
        result.analog_diagnostics = self._format_configured_diagnostics(diagnostics)
        if monotonic() >= deadline and len(analogues) < limit:
            result.market_error = (
                f"Поиск аналогов остановлен по лимиту {self.market_time_limit:g} сек.: "
                f"проверено {checked}, найдено {len(analogues)}."
            )
        elif not analogues:
            details = result.analog_diagnostics or "поисковая выдача не дала подходящих кандидатов"
            result.market_error = (
                f"Проверено {checked} карточек Ozon, подходящих аналогов не найдено. "
                f"Причины: {details}."
            )

        # Price deliberately does not participate in ranking: otherwise the
        # comparison would select convenient prices instead of similar goods.
        analogues.sort(
            key=lambda item: (
                -(item.similarity_score or 0.0),
                -(item.rating or 0.0),
                -(item.feedbacks or 0),
            )
        )
        return analogues[:limit]

    def _format_configured_diagnostics(self, diagnostics: Counter[str]) -> str:
        labels = {
            "category_mismatch": "другая категория",
            "missing_price": "не определена цена",
            "card_error": "не загружена карточка/характеристики",
            **rejection_labels(self.matching_settings),
        }
        return "; ".join(
            f"{labels.get(reason, reason)} — {count}"
            for reason, count in diagnostics.most_common()
            if count > 0
        )
