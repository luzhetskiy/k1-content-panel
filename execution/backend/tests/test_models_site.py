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
