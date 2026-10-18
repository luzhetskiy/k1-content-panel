import pytest

from app.ai.text import TextResult
from app.house_projects.markup import find_description, plain_text
from app.house_projects.rewriter import NO_DESCRIPTION, RewriteError, rewrite_page
from app.models.house_project import HouseProjectPage
from app.models.site import Site
from app.seed import seed_prompts
from app.sites.client import SiteAPIError

ORIGINAL = ("<p>Представляем проект уютного дома площадью 82.64 м², идеально подходящего "
            "для семьи из четырёх человек.</p>"
            "<h3>Ключевые преимущества:</h3>"
            "<ul><li>Монолитная плита под всем домом.</li>"
            "<li>Терраса 9.06 м² для отдыха.</li></ul>")
GOOD = ("<p>Перед вами комфортный дом на 82,64 м², который отлично подойдёт "
        "семье, где растут двое детей.</p>"
        "<h3>Почему выбирают проект:</h3>"
        "<ul><li>Под зданием залита монолитная плита.</li>"
        "<li>Для отдыха есть терраса 9.06 м².</li></ul>")
BAD = GOOD.replace("9.06", "девять")


def page_html(inner):
    return ('<section class="static-project">\n<!-- Хар-ки и описание -->\n'
            '<section class="project-chars"><ul class="char"><li>Площадь 82</li></ul></section>\n'
            '<section class="product-info__section project-description"><h2>Описание</h2>\n'
            f'<div class="amk-projects__about-1 product-info__text">\n{inner}\n</div>\n'
            '</section>\n{% custom_form "forma-rasschet" %}\n</section>')


class FakeSite:
    def __init__(self, inner, *, store=lambda html: html, patch_error=None,
                 stored_on_error=True):
        self.text = page_html(inner) if inner is not None else "<p>без блока</p>"
        self.patches = []
        self.store = store                    # как сайт сохраняет присланный HTML
        self.patch_error = patch_error        # ошибка PATCH после отправки тела
        self.stored_on_error = stored_on_error

    def get_page(self, page_id):
        return {"id": page_id, "title": "Проект дома из кирпича", "url": "/pr/k/",
                "text": self.text}

    def update_page_text(self, page_id, html, *, as_is=False):
        assert as_is
        self.patches.append(html)
        if self.patch_error is None or self.stored_on_error:
            self.text = self.store(html)
        if self.patch_error is not None:
            raise self.patch_error
        return {"id": page_id}


class FakeLLM:
    def __init__(self, *answers, during=None):
        self.answers = list(answers)
        self.prompts = []
        self.during = during      # что происходит на сайте, пока модель пишет

    def complete_text(self, prompt, *, reasoning=True):
        assert reasoning is False
        self.prompts.append(prompt)
        if self.during:
            self.during()
        return TextResult(self.answers.pop(0), 100, 50, 0.5)


@pytest.fixture
def site(db_session):
    seed_prompts(db_session)
    row = Site(name="Стройбаза", domain="s.ru", base_url="https://s.ru", api_token_enc="e",
               tone_of_voice="спокойный", projects_enabled=True, projects_parent_id=15)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def page(db_session, site):
    row = HouseProjectPage(site_id=site.id, remote_id=16, status="in_work")
    db_session.add(row)
    db_session.commit()
    return row


def run(db, page, site, client, llm, usage=None):
    rewrite_page(db, page, site, site_client=client, text_client=llm,
                 record_usage=lambda *a: (usage if usage is not None else []).append(a),
                 sleep=lambda s: None)


def test_first_rewrite_saves_original_and_writes_site(db_session, site, page):
    client, llm, usage = FakeSite(ORIGINAL), FakeLLM(GOOD), []
    before = client.text
    run(db_session, page, site, client, llm, usage)
    assert page.original_html == ORIGINAL
    assert (page.rewritten_html, page.status, page.rewrite_count) == (GOOD, "done", 1)
    assert page.rewritten_at is not None and page.title == "Проект дома из кирпича"
    block = find_description(before)
    assert client.patches[0] == before[:block.start] + "\n" + GOOD + "\n" + before[block.end:]
    assert usage == [(100, 50, 0.5)]
    assert "82.64 м²" in llm.prompts[0]
    assert f"около {len(plain_text(ORIGINAL))} символов" in llm.prompts[0]
    assert "Тон сайта: спокойный" in llm.prompts[0]


def test_repeat_rewrites_from_original(db_session, site, page):
    client = FakeSite(ORIGINAL)
    run(db_session, page, site, client, FakeLLM(GOOD))
    second = GOOD.replace("комфортный", "удобный")
    llm = FakeLLM(second)
    run(db_session, page, site, client, llm)
    assert page.original_html == ORIGINAL
    assert "Представляем проект" in llm.prompts[0]
    assert (page.rewritten_html, page.rewrite_count) == (second, 2)


def test_manual_edit_becomes_new_original(db_session, site, page):
    client = FakeSite(ORIGINAL)
    run(db_session, page, site, client, FakeLLM(GOOD))
    edited = ORIGINAL.replace("82.64", "84.10")
    client.text = page_html(edited)
    llm = FakeLLM(GOOD.replace("82,64", "84,10"))
    run(db_session, page, site, client, llm)
    assert page.original_html == edited
    assert "84.10" in llm.prompts[0] and page.rewrite_count == 2


def test_whitespace_change_is_not_an_edit(db_session, site, page):
    client = FakeSite(ORIGINAL)
    run(db_session, page, site, client, FakeLLM(GOOD))
    client.text = page_html(GOOD.replace("</p>", "</p>\n   ").replace("</li>", "</li>\n"))
    run(db_session, page, site, client, FakeLLM(GOOD))
    assert page.original_html == ORIGINAL


def test_retry_with_violations_then_success(db_session, site, page):
    llm = FakeLLM(BAD, GOOD)
    run(db_session, page, site, FakeSite(ORIGINAL), llm)
    assert page.status == "done"
    assert "пропали числа из оригинала: 9.06" in llm.prompts[1]
    assert "Предыдущий вариант не прошёл проверку" not in llm.prompts[0]


def test_two_failures_raise_and_do_not_touch_site(db_session, site, page):
    client = FakeSite(ORIGINAL)
    with pytest.raises(RewriteError, match="пропали числа"):
        run(db_session, page, site, client, FakeLLM(BAD, BAD))
    assert client.patches == []
    assert page.original_html == ORIGINAL      # оригинал сохранён до вызова LLM


def test_page_without_description_is_skipped(db_session, site, page):
    for client in (FakeSite(None), FakeSite("  <p> </p>  ")):
        llm = FakeLLM()
        page.status = "in_work"
        run(db_session, page, site, client, llm)
        assert (page.status, page.skip_reason) == ("skipped", NO_DESCRIPTION)
        assert llm.prompts == [] and client.patches == []


def test_rest_of_page_edited_during_rewrite_is_kept(db_session, site, page):
    client = FakeSite(ORIGINAL)

    def edit_chars():
        client.text = client.text.replace("Площадь 82", "Площадь 82, гараж")

    run(db_session, page, site, client, FakeLLM(GOOD, during=edit_chars))
    assert "Площадь 82, гараж" in client.patches[0] and GOOD in client.patches[0]


def test_description_edited_during_rewrite_is_not_overwritten(db_session, site, page):
    client = FakeSite(ORIGINAL)

    def edit_description():
        client.text = page_html(ORIGINAL.replace("82.64", "84.10"))

    with pytest.raises(RewriteError, match="изменилось во время рерайта"):
        run(db_session, page, site, client, FakeLLM(GOOD, during=edit_description))
    assert client.patches == []


def test_site_normalized_markup_is_remembered_as_ours(db_session, site, page):
    client = FakeSite(ORIGINAL, store=lambda html: html.replace("<li>", '<li class="x">'))
    run(db_session, page, site, client, FakeLLM(GOOD))
    assert '<li class="x">' in page.rewritten_html
    # следующий запуск узнаёт свой текст и переписывает от прежнего оригинала
    run(db_session, page, site, client, FakeLLM(GOOD))
    assert page.original_html == ORIGINAL


def test_lost_patch_response_but_text_stored_counts_as_done(db_session, site, page):
    client = FakeSite(ORIGINAL, patch_error=SiteAPIError("сеть недоступна", status_code=None))
    run(db_session, page, site, client, FakeLLM(GOOD))
    assert (page.status, page.rewritten_html, page.rewrite_count) == ("done", GOOD, 1)


def test_patch_failure_leaves_page_untouched(db_session, site, page):
    for error in (SiteAPIError("сеть недоступна", status_code=None),
                  SiteAPIError("HTTP 400", status_code=400)):
        client = FakeSite(ORIGINAL, patch_error=error, stored_on_error=False)
        with pytest.raises(SiteAPIError):
            run(db_session, page, site, client, FakeLLM(GOOD))
        assert (page.rewritten_html, page.rewrite_count, page.rewritten_at) == ("", 0, None)
