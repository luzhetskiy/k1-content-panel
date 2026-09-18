"""Парсинг выгрузки Яндекс.Карт (xlsx). Портирует build_header_map/site_key
из execution/step1_import_yandex.py; category_raw дополнительно обрезается
до первого сегмента перед '|' — см. directions/2026-08-06-builders-import-design.md §2."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import openpyxl

COLUMNS = {
    "name": "Название",
    "category": "Категории",
    "region": "Регион",
    "city": "Город",
    "address": "Полный адрес",
    "phone_mobile": "Мобильные",
    "phone_landline": "Немобильные",
    "phone_all": "Все телефоны",
    "site": "Сайт",
    "email": "Email с сайта компании",
    "working_hours": "График",
    "logo_url": "Логотип",
    "lat": "Широта",
    "lon": "Долгота",
    "ratings": "Оценок",
    "reviews": "Отзывов",
    "rating": "Рейтинг",
    "yandex_card": "Карточка организации",
}

REQUIRED_KEYS = ("name", "region", "city", "category", "site")


class XlsxParseError(RuntimeError):
    pass


@dataclass
class ParsedRow:
    site_key: str
    website_raw: str
    name: str
    region_raw: str
    category_raw: str
    city: str
    address: str = ""
    phone: str = ""
    email: str = ""
    working_hours: str = ""
    logo_url: str = ""
    rating: float | None = None
    reviews_count: int = 0
    ratings_count: int = 0
    lat: float | None = None
    lon: float | None = None
    yandex_url: str = ""
    raw_row: dict = field(default_factory=dict)


def site_key(url: str) -> str:
    """Нормализованный ключ сайта: без схемы, www, слэша, регистра."""
    if not url:
        return ""
    s = str(url).lower().strip()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def _first_segment(value) -> str:
    """Первый сегмент значения вида «А | Б | В» до первой вертикальной
    черты — общий контракт колонок с несколькими значениями через «|»
    (сайт, категории, логотип, фото)."""
    if not value:
        return ""
    return str(value).split("|")[0].strip()


def _normalize_site_url(url) -> str:
    if not url:
        return ""
    raw = _first_segment(url)
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _category_first_segment(value) -> str:
    """Колонка «Категории» — берём только первый сегмент до «|»: остальное —
    уточнения источника, вносящие путаницу в справочник категорий."""
    return _first_segment(value)


def _logo_url(value) -> str:
    """Логотип из выгрузки безусловно перекрывает найденный скрейпингом
    (см. задачи 2-5 плана), поэтому не пропускаем мусор дальше: числовая
    или текстовая ячейка («нет логотипа») заменила бы рабочий логотип
    битой картинкой."""
    url = _first_segment(value)
    return url if url.startswith(("http://", "https://")) else ""


def _first_email(value) -> str:
    """Выгрузка склеивает все адреса с сайта через «,» или «;» (до 34 штук на
    боевом файле), а на странице email — одна ссылка mailto:. Берём первый."""
    return re.split(r"[,;]", str(value or ""))[0].strip()


def _first_nonempty(row: tuple, header: dict, *keys: str) -> str:
    """Первое непустое значение после strip. Сырая проверка на истинность
    не годится: ячейка из одних пробелов оборвала бы цепочку запасных
    колонок телефона, хотя значения в ней нет."""
    for key in keys:
        value = str(_get(row, header, key) or "").strip()
        if value:
            return value
    return ""


def _to_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _to_int(val) -> int:
    try:
        return int(float(str(val).replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def _build_header_map(header_row: tuple) -> dict:
    title_to_idx = {}
    for idx, title in enumerate(header_row):
        if title is not None:
            title_to_idx[str(title).strip()] = idx
    mapping = {key: title_to_idx[title] for key, title in COLUMNS.items()
              if title in title_to_idx}
    missing = [COLUMNS[k] for k in REQUIRED_KEYS if k not in mapping]
    if missing:
        raise XlsxParseError(f"В файле нет обязательных колонок: {missing}")
    return mapping


def _get(row: tuple, header: dict, key: str):
    idx = header.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def parse_workbook(data: bytes) -> list[ParsedRow]:
    """Парсит xlsx целиком. Строки без сайта отбрасываются. При дублях
    site_key внутри файла остаётся последняя встреченная строка."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise XlsxParseError("файл пуст — нет строки заголовка")
        header = _build_header_map(header_row)

        by_key: dict[str, ParsedRow] = {}
        for row in rows_iter:
            website_raw = _normalize_site_url(_get(row, header, "site"))
            if not website_raw:
                continue
            key = site_key(website_raw)
            if not key:
                continue

            name = str(_get(row, header, "name") or "").strip()
            if not name:
                continue

            # Порядок «Немобильные» → «Мобильные» → «Все телефоны» выбран
            # осознанно и расходится со старым CLI-скриптом
            # (execution/step1_import_yandex.py, first_phone), который
            # пробовал «Все телефоны» первой: по реальной выгрузке из 5407
            # непустых значений «Все телефоны» 13% содержат кириллическую
            # пометку («Отдел продаж», «Канцелярия»), а 39% — несколько
            # номеров через «|». Приоритетные колонки чище.
            phone = _first_segment(_first_nonempty(
                row, header, "phone_landline", "phone_mobile", "phone_all"))

            raw_row = {}
            for k in header:
                val = _get(row, header, k)
                raw_row[str(k)] = val if isinstance(val, (str, int, float, type(None))) else str(val)

            by_key[key] = ParsedRow(
                site_key=key,
                website_raw=website_raw,
                name=name,
                region_raw=str(_get(row, header, "region") or "").strip(),
                category_raw=_category_first_segment(_get(row, header, "category")),
                city=str(_get(row, header, "city") or "").strip(),
                address=str(_get(row, header, "address") or "").strip(),
                phone=phone,
                email=_first_email(_get(row, header, "email")),
                working_hours=str(_get(row, header, "working_hours") or "").strip(),
                logo_url=_logo_url(_get(row, header, "logo_url")),
                rating=_to_float(_get(row, header, "rating")),
                reviews_count=_to_int(_get(row, header, "reviews")),
                ratings_count=_to_int(_get(row, header, "ratings")),
                lat=_to_float(_get(row, header, "lat")),
                lon=_to_float(_get(row, header, "lon")),
                yandex_url=str(_get(row, header, "yandex_card") or "").strip(),
                raw_row=raw_row,
            )
        return list(by_key.values())
    finally:
        wb.close()
