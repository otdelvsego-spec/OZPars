"""Client for public Ozon storefront pages."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import json
import re
from threading import local
from time import monotonic
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .characteristics import REJECTION_LABELS, evaluate_similarity, extract_characteristics
from .models import MarketProduct, PriceResult, Product

AnalogueProgress = Callable[[int, int, int], None]
_PRODUCT_HREF_RE = re.compile(r"/product/(?:[^/?#]*-)?(\d+)/?", re.IGNORECASE)
_PRICE_RE = re.compile(r"(\d[\d\s\u00a0\u202f]*)\s*₽")


class OzonClient:
    BASE_URL = "https://www.ozon.ru"

    def __init__(
        self,
        timeout: float = 15.0,
        market_time_limit: float = 60.0,
        candidate_check_limit: int = 20,
        session: requests.Session | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Тайм-аут должен быть больше нуля.")
        if market_time_limit <= 0:
            raise ValueError("Лимит сравнения должен быть больше нуля.")
        if candidate_check_limit < 1 or candidate_check_limit > 30:
            raise ValueError("Количество проверяемых кандидатов должно быть от 1 до 30.")
        self.timeout = timeout
        self.market_time_limit = market_time_limit
        self.candidate_check_limit = candidate_check_limit
        self._session_override = session
        self._thread_local = local()

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        }

    def _get_session(self) -> requests.Session:
        if self._session_override is not None:
            return self._session_override
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._headers())
            self._thread_local.session = session
        return session

    def get_product(self, product: Product) -> PriceResult:
        sku = product.sku.strip()
        if not sku.isdigit():
            raise ValueError(f"Некорректный артикул Ozon: {sku!r}")
        url, html = self._load_product_page(sku)
        parsed = self._parse_product_page(sku, url, html)
        return PriceResult(
            sku=sku,
            input_name=product.name,
            barcode=product.barcode,
            ozon_name=parsed["name"],
            brand=parsed["brand"],
            category_name=parsed["category"],
            current_price=parsed["current_price"],
            ozon_card_price=parsed["ozon_card_price"],
            price_without_card=parsed["price_without_card"],
            old_price=parsed["old_price"],
            rating=parsed["rating"],
            feedbacks=parsed["feedbacks"],
            url=url,
        )

    def enrich_product_characteristics(self, result: PriceResult) -> PriceResult:
        try:
            raw = self._load_features(result.url)
            result.characteristics = extract_characteristics(raw)
            if not result.characteristics.has_data:
                result.characteristics_error = (
                    "На странице характеристик Ozon не найдены габариты, вес или материалы."
                )
        except (requests.RequestException, ValueError, RuntimeError) as error:
            result.characteristics_error = str(error)
        return result

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
                    result.category_name
                    and candidate_result.category_name
                    and self._normalize_category(result.category_name)
                    != self._normalize_category(candidate_result.category_name)
                ):
                    diagnostics["category_mismatch"] += 1
                    continue

                score, matched_fields, reason = evaluate_similarity(
                    result.characteristics,
                    candidate_result.characteristics,
                )
                if score is None:
                    diagnostics[reason or "total_below_threshold"] += 1
                else:
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
        result.analog_diagnostics = self._format_diagnostics(diagnostics)
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

        analogues.sort(
            key=lambda item: (
                -(item.similarity_score or 0.0),
                abs(item.current_price - (result.current_price or 0.0)),
            )
        )
        return analogues[:limit]

    def _load_product_page(self, sku: str) -> tuple[str, str]:
        direct_url = f"{self.BASE_URL}/product/{sku}/"
        try:
            response = self._request(direct_url)
            if self._url_contains_sku(response.url, sku):
                return response.url, response.text
        except (requests.RequestException, RuntimeError):
            pass

        search_url = f"{self.BASE_URL}/search/?text={quote_plus(sku)}"
        response = self._request(search_url)
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href"))
            match = _PRODUCT_HREF_RE.search(href)
            if match and match.group(1) == sku:
                url = urljoin(self.BASE_URL, href.split("?")[0])
                product_response = self._request(url)
                return product_response.url, product_response.text
        raise RuntimeError(f"Товар Ozon с артикулом {sku} не найден в публичной витрине.")

    def _load_features(self, product_url: str) -> dict[str, str]:
        clean_url = self._canonical_product_url(product_url)
        features_url = clean_url.rstrip("/") + "/features/"
        response = self._request(features_url)
        return self._parse_features_page(response.text)

    def _search_candidate_skus(self, result: PriceResult, pool_limit: int) -> list[str]:
        queries: list[str] = []
        for value in (
            result.ozon_name,
            result.input_name,
            self._simplify_name(result.ozon_name or result.input_name),
            result.category_name,
        ):
            cleaned = value.strip()
            if cleaned and cleaned.lower() not in {item.lower() for item in queries}:
                queries.append(cleaned)
        if not queries:
            raise ValueError("Не удалось сформировать поисковый запрос для аналогов Ozon.")

        found: list[str] = []
        seen: set[str] = set()
        quota = max(8, pool_limit // len(queries))
        last_error: Exception | None = None
        for query in queries[:4]:
            try:
                response = self._request(
                    f"{self.BASE_URL}/search/?text={quote_plus(query)}"
                )
            except (requests.RequestException, RuntimeError) as error:
                last_error = error
                continue
            added = 0
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                match = _PRODUCT_HREF_RE.search(str(anchor.get("href")))
                if not match:
                    continue
                sku = match.group(1)
                if sku in seen:
                    continue
                seen.add(sku)
                found.append(sku)
                added += 1
                if len(found) >= pool_limit or added >= quota:
                    break
            if len(found) >= pool_limit:
                break
        if not found and last_error is not None:
            raise RuntimeError(f"Поиск Ozon недоступен: {last_error}") from last_error
        return found

    def _request(self, url: str) -> requests.Response:
        response = self._get_session().get(url, timeout=self.timeout, allow_redirects=True)
        if response.status_code in (403, 429):
            raise RuntimeError(
                "Ozon ограничил автоматический доступ к публичной витрине. "
                "Повторите позже или смените интернет-соединение."
            )
        response.raise_for_status()
        self._check_challenge(response.text)
        return response

    @staticmethod
    def _check_challenge(html: str) -> None:
        text = html.lower()
        challenge_markers = (
            "captcha",
            "подтвердите, что вы не робот",
            "доступ ограничен",
            "access denied",
        )
        if any(marker in text for marker in challenge_markers):
            raise RuntimeError(
                "Ozon показал защитную проверку вместо карточки товара. "
                "Автоматический анализ временно недоступен для этого соединения."
            )

    @classmethod
    def _parse_product_page(cls, sku: str, url: str, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        product_json = cls._product_json_ld(soup)
        name = cls._json_text(product_json.get("name"))
        if not name:
            title = soup.find("meta", attrs={"property": "og:title"})
            name = str(title.get("content", "")).strip() if title else ""
        brand_value = product_json.get("brand")
        brand = cls._json_text(
            brand_value.get("name") if isinstance(brand_value, dict) else brand_value
        )
        category = cls._breadcrumb_category(soup)
        text = soup.get_text("\n", strip=True)
        prices = cls._extract_prices(text, product_json)
        aggregate = product_json.get("aggregateRating")
        rating = None
        feedbacks = None
        if isinstance(aggregate, dict):
            rating = cls._optional_float(aggregate.get("ratingValue"))
            feedbacks = cls._optional_int(
                aggregate.get("reviewCount", aggregate.get("ratingCount"))
            )
        if prices["current_price"] is None:
            raise RuntimeError(
                f"Не удалось определить цену артикула Ozon {sku}. "
                "Возможно, Ozon изменил формат публичной карточки."
            )
        return {
            "name": name,
            "brand": brand,
            "category": category,
            **prices,
            "rating": rating,
            "feedbacks": feedbacks,
            "url": url,
        }

    @classmethod
    def _parse_features_page(cls, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        raw: dict[str, str] = {}

        product_json = cls._product_json_ld(soup)
        properties = product_json.get("additionalProperty")
        if isinstance(properties, list):
            for item in properties:
                if isinstance(item, dict):
                    name = cls._json_text(item.get("name"))
                    value = cls._json_text(item.get("value"))
                    if name and value:
                        raw[name] = value

        for row in soup.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0] and cells[1]:
                raw.setdefault(cells[0], cells[1])
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                label = dt.get_text(" ", strip=True)
                value = dd.get_text(" ", strip=True)
                if label and value:
                    raw.setdefault(label, value)

        lines = [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]
        hints = (
            "материал", "состав", "корпус", "фасад", "столешниц", "каркас",
            "размер упаков", "ширина", "высота", "глубина", "длина", "вес", "масса",
        )
        for index, line in enumerate(lines[:-1]):
            normalized = line.lower().replace("ё", "е")
            if not any(hint in normalized for hint in hints):
                continue
            value = lines[index + 1]
            if value and value.lower() != line.lower() and len(value) <= 250:
                raw.setdefault(line, value)
        return raw

    @classmethod
    def _extract_prices(cls, text: str, product_json: dict[str, Any]) -> dict[str, float | None]:
        normalized = text.replace("\u00a0", " ").replace("\u202f", " ")
        ozon_card = cls._price_before_marker(normalized, "с Ozon Картой")
        without_card = cls._price_before_marker(normalized, "без Ozon Карты")
        offers = product_json.get("offers")
        json_price = None
        old_price = None
        if isinstance(offers, dict):
            json_price = cls._optional_float(offers.get("price", offers.get("lowPrice")))
            high_price = cls._optional_float(offers.get("highPrice"))
            if high_price and json_price and high_price > json_price:
                old_price = high_price
        current = ozon_card or without_card or json_price
        return {
            "current_price": current,
            "ozon_card_price": ozon_card,
            "price_without_card": without_card or json_price,
            "old_price": old_price,
        }

    @classmethod
    def _price_before_marker(cls, text: str, marker: str) -> float | None:
        marker_index = text.lower().find(marker.lower())
        if marker_index < 0:
            return None
        prefix = text[max(0, marker_index - 80):marker_index]
        prices = _PRICE_RE.findall(prefix)
        return cls._parse_price(prices[-1]) if prices else None

    @staticmethod
    def _parse_price(value: str) -> float | None:
        digits = re.sub(r"\D", "", value)
        return float(digits) if digits else None

    @classmethod
    def _product_json_ld(cls, soup: BeautifulSoup) -> dict[str, Any]:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or script.get_text())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for item in cls._walk_json_ld(payload):
                item_type = item.get("@type")
                types = item_type if isinstance(item_type, list) else [item_type]
                if "Product" in types:
                    return item
        return {}

    @classmethod
    def _breadcrumb_category(cls, soup: BeautifulSoup) -> str:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or script.get_text())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for item in cls._walk_json_ld(payload):
                if item.get("@type") != "BreadcrumbList":
                    continue
                elements = item.get("itemListElement")
                if isinstance(elements, list):
                    names = []
                    for element in elements:
                        if isinstance(element, dict):
                            names.append(cls._json_text(element.get("name")))
                    names = [name for name in names if name]
                    if names:
                        return names[-1]
        return ""

    @classmethod
    def _walk_json_ld(cls, payload: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            result.append(payload)
            graph = payload.get("@graph")
            if isinstance(graph, list):
                result.extend(item for item in graph if isinstance(item, dict))
        elif isinstance(payload, list):
            result.extend(item for item in payload if isinstance(item, dict))
        return result

    @staticmethod
    def _canonical_product_url(url: str) -> str:
        split = urlsplit(url)
        path = split.path
        match = _PRODUCT_HREF_RE.search(path)
        if match:
            end = match.end()
            path = path[:end]
        return urlunsplit((split.scheme or "https", split.netloc or "www.ozon.ru", path, "", ""))

    @staticmethod
    def _url_contains_sku(url: str, sku: str) -> bool:
        match = _PRODUCT_HREF_RE.search(urlsplit(url).path)
        return bool(match and match.group(1) == sku)

    @staticmethod
    def _simplify_name(value: str) -> str:
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", value)
        stopwords = {
            "для", "и", "в", "на", "с", "со", "из", "цвет", "белый", "черный",
            "чёрный", "серый", "бежевый", "коричневый", "ozon", "оригинал",
        }
        useful = [word for word in words if word.lower() not in stopwords]
        return " ".join(useful[:7])

    @staticmethod
    def _normalize_category(value: str) -> str:
        return " ".join(value.lower().replace("ё", "е").split())

    @staticmethod
    def _json_text(value: Any) -> str:
        return "" if value is None else str(value).strip()

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_diagnostics(diagnostics: Counter[str]) -> str:
        labels = {
            "category_mismatch": "другая категория",
            "missing_price": "не определена цена",
            "card_error": "не загружена карточка/характеристики",
            **REJECTION_LABELS,
        }
        return "; ".join(
            f"{labels.get(reason, reason)} — {count}"
            for reason, count in diagnostics.most_common()
            if count > 0
        )
