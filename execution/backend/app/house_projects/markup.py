"""Блок описания на странице проекта дома: поиск, замена, видимый текст.

Описание — содержимое `div.product-info__text` внутри
`section.project-description`. Меняется только оно, и по позициям в исходной
строке, без пересериализации HTML: разметка страниц бывает двух видов (с
отступами и в одну строку, `<br />` вместо `<br>`), и любой парсер, который
собирает HTML обратно, переписал бы страницу целиком — вместе с характеристиками
и шаблонным тегом `{% custom_form %}`."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_OPEN_TAG = r"<{tag}\b[^>]*>"
_CLASS = re.compile(r"""\bclass\s*=\s*(["'])(.*?)\1""", re.DOTALL)
_DIV_TOKEN = re.compile(r"<div\b[^>]*>|</div\s*>", re.IGNORECASE)
_FENCED = re.compile(r"```[\w-]*[ \t]*\r?\n?(.*?)\r?\n?[ \t]*```", re.DOTALL)


@dataclass
class DescriptionBlock:
    start: int        # первый символ содержимого — сразу после открывающего <div>
    end: int          # позиция закрывающего </div>
    inner_html: str


def _with_class(page_html: str, tag: str, css_class: str) -> list[re.Match]:
    found = []
    for match in re.finditer(_OPEN_TAG.format(tag=tag), page_html, re.IGNORECASE):
        attr = _CLASS.search(match.group(0))
        if attr and css_class in attr.group(2).split():
            found.append(match)
    return found


def _closing_div(page_html: str, start: int) -> int | None:
    """Позиция </div>, закрывающего div, содержимое которого начинается со start."""
    depth = 1
    for token in _DIV_TOKEN.finditer(page_html, start):
        depth += -1 if token.group(0).startswith("</") else 1
        if depth == 0:
            return token.start()
    return None


def find_description(page_html: str) -> DescriptionBlock | None:
    """None — блока нет или их больше одного: какой из них описание, не угадываем."""
    sections = _with_class(page_html, "section", "project-description")
    blocks = _with_class(page_html, "div", "product-info__text")
    if len(sections) != 1 or len(blocks) != 1 or blocks[0].start() < sections[0].end():
        return None
    start = blocks[0].end()
    end = _closing_div(page_html, start)
    if end is None:
        return None
    return DescriptionBlock(start=start, end=end, inner_html=page_html[start:end])


def replace_description(page_html: str, block: DescriptionBlock, new_inner: str) -> str:
    return page_html[:block.start] + "\n" + new_inner.strip() + "\n" + page_html[block.end:]


def clean_model_html(raw: object) -> str:
    """Если модель обернула ответ в ```html, берётся только содержимое блока:
    пояснения до и после него на сайт не попадают. Остальное оценивает проверка."""
    text = str(raw or "").strip()
    fenced = _FENCED.search(text)
    return (fenced.group(1) if fenced else text).strip()


def plain_text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _normalized(fragment: str) -> str:
    fragment = html.unescape(fragment)
    fragment = re.sub(r">\s+<", "><", fragment)
    fragment = re.sub(r"<br\s*/?>", "<br>", fragment, flags=re.IGNORECASE)
    return " ".join(fragment.split())


def same_html(first: str, second: str) -> bool:
    """Совпадение без учёта пробелов, переносов между тегами, `<br />` против
    `<br>` и сущностей (`&nbsp;`, `&laquo;`) против самих символов: админка
    сайта переформатирует разметку при сохранении, и это не правка текста."""
    return _normalized(first) == _normalized(second)
