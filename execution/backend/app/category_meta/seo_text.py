"""SEO-текст категории: чистка HTML, который вернула модель, и проверка.

Длина считается по видимому тексту без тегов. Просим у модели 2500–3000
символов, принимаем с допуском: модели плохо считают символы, и текст на 2400
не повод терять хорошие теги вместе с ним."""

from __future__ import annotations

import html
import re

from app.category_meta.validate import FORBIDDEN_CLAIMS, MetaContext, _norm

SEO_TEXT_TARGET = (2500, 3000)
SEO_TEXT_MIN = 2300
SEO_TEXT_MAX = 3300

ALLOWED_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "em", "br"}
# h1 на странице уже есть (из метатега) — второй заголовок первого уровня
# превращаем в h2, а не выкидываем вместе с текстом.
RENAMED_TAGS = {"h1": "h2", "b": "strong", "i": "em"}

_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9]+)[^>]*?(/?)\s*>")
_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$")


def clean_seo_html(raw: object) -> str:
    """Оставляет только разметку текста без атрибутов: ссылки, стили и
    скрипты модели на странице магазина не нужны. Содержимое чужих тегов
    сохраняется, кроме script/style."""
    text = _FENCE.sub("", str(raw or "").strip())
    text = re.sub(r"<(script|style)\b.*?</\1\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)

    def tag(match: re.Match) -> str:
        closing, name = match.group(1), match.group(2).lower()
        name = RENAMED_TAGS.get(name, name)
        if name not in ALLOWED_TAGS:
            return ""
        if name == "br":
            return "<br>"
        return f"<{closing}{name}>"

    return _TAG.sub(tag, text).strip()


def plain_text(seo_html: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", seo_html)).split())


def validate_seo_text(seo_html: str, ctx: MetaContext) -> list[str]:
    errors: list[str] = []
    text = _norm(plain_text(seo_html))
    low, high = SEO_TEXT_TARGET
    if not SEO_TEXT_MIN <= len(text) <= SEO_TEXT_MAX:
        errors.append(f"SEO-текст должен быть {low}–{high} символов без тегов ({len(text)})")
    if "<h2>" not in seo_html:
        errors.append("в SEO-тексте нет подзаголовков <h2>")
    if _norm(ctx.form_nominative) not in text:
        errors.append(f"в SEO-тексте нет фразы «{ctx.form_nominative}»")
    if _norm(ctx.city_in) not in text:
        errors.append(f"в SEO-тексте нет «{ctx.city_in}»")
    site_description = _norm(ctx.site_description)
    for label, pattern in FORBIDDEN_CLAIMS.items():
        if re.search(pattern, text) and not re.search(pattern, site_description):
            errors.append(f"в SEO-тексте утверждение «{label}», которого нет в описании сайта")
    hits = [stop for stop in ctx.stoplist if stop in text]
    if hits:
        errors.append(f"в SEO-тексте слова из стоп-листа: {', '.join(hits)}")
    return errors
