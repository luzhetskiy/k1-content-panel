import pytest
from sqlalchemy import Integer
from sqlalchemy.exc import IntegrityError

from app.models.site import Site


def test_site_minimal_fields():
    site = Site(name="Стройбаза Самара", domain="stroybaza-samara.ru",
                base_url="https://stroybaza-samara.ru", api_token_enc="enc")
    assert site.__tablename__ == "sites"
    assert site.domain == "stroybaza-samara.ru"


def test_site_article_settings():
    """Раздел задаётся id родительской страницы; её url подтягивается синхронизацией,
    руками не вводится. `/blog/` — частный случай, а не требование."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                publish_target="pages", articles_parent_id=25,
                articles_url_prefix="/poleznye-stati/", reference_article_id=312)
    assert site.articles_parent_id == 25
    assert site.articles_url_prefix == "/poleznye-stati/"


def test_site_reference_cache_fields():
    """Число картинок не настраивается — оно равно числу <img> в эталоне."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                reference_article_id=312, reference_html="<p>x</p><img><img>",
                reference_images=2)
    assert site.reference_images == 2
    assert not hasattr(Site, "images_per_article")
    assert not hasattr(Site, "article_template_html")


def test_site_content_profile():
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                site_description="Строительная база в Самаре, аудитория — частные "
                                 "застройщики",
                tone_of_voice="практичный, без рекламных обещаний")
    assert "Самаре" in site.site_description
    assert "практичный" in site.tone_of_voice


def test_site_builder_teaser_taxonomy():
    """category/city/location — это карточки-тизеры каталога строителей
    (addresses-services), к обложке статьи отношения не имеют."""
    site = Site(name="X", domain="x.ru", base_url="https://x.ru", api_token_enc="e",
                teaser_category_id=3, teaser_city_id=2, teaser_location_id=1)
    assert (site.teaser_category_id, site.teaser_city_id, site.teaser_location_id) == (3, 2, 1)


def test_reference_images_is_integer():
    """Число картинок равно числу <img> в эталоне — строка здесь молча
    сломала бы арифметику при сборке статьи."""
    assert isinstance(Site.__table__.c.reference_images.type, Integer)


def test_domain_is_unique(db_session):
    db_session.add(Site(name="A", domain="dup.ru", base_url="https://dup.ru",
                         api_token_enc="e"))
    db_session.commit()

    db_session.add(Site(name="B", domain="dup.ru", base_url="https://dup.ru",
                         api_token_enc="e"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_domain_is_normalized_on_assignment():
    """DNS регистр не различает, а колонка — различает: без нормализации
    example.ru и Example.ru завелись бы как два разных сайта."""
    site = Site(name="X", domain="  Example.RU  ", base_url="https://example.ru",
                api_token_enc="e")
    assert site.domain == "example.ru"


def test_normalized_domain_collides_with_existing(db_session):
    """Нормализация в модели — на любом пути записи, а не только там, где о ней
    вспомнили: разный регистр не должен давать два сайта на один домен."""
    db_session.add(Site(name="A", domain="example.ru", base_url="https://example.ru",
                         api_token_enc="e"))
    db_session.commit()

    db_session.add(Site(name="B", domain="Example.ru", base_url="https://example.ru",
                         api_token_enc="e"))
    with pytest.raises(IntegrityError):
        db_session.commit()
