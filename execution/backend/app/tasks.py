"""Фоновые задачи. Каждая обёрнута парой sync-функций: сама задача открывает
сессию, а логика живёт в `*_sync(db, ...)` — так её можно тестировать без брокера.
"""

from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.ai.factory import AIConfigError, build_text_client
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.api.admin_sites import open_client as open_site_client
from app.articles.builder import build_for
from app.articles.topics import filter_duplicates
from app.celery_app import celery_app
from app.clock import utcnow
from app.companies.builder import build_for as build_for_company
from app.db import SessionLocal
from app.models.article import Article, ArticleBatch
from app.models.company import Company, CompanyBatch
from app.models.job import JobRun
from app.models.site import Site
from app.settings.crypto import SecretDecryptionError
from app.sites.client import SiteAPIError

# Статусы партии, из которых имеет смысл (пере)генерировать темы — находка №3
# ревью Task 17. topics_pending — обычный старт; failed — ручной перезапуск
# после починки причины отказа (например, админ вписал ключ RouterAI).
# В обоих случаях у партии гарантированно нет ни одной Article: успешный
# прогон добавляет Article и сразу переводит статус в topics_review одним
# коммитом (см. generate_topics_sync ниже) — состояния «темы уже есть, но
# статус ещё topics_pending/failed» в этом коде не возникает.
_TOPICS_RUNNABLE_STATUSES = ("topics_pending", "failed")

# Сколько ДОПОЛНИТЕЛЬНЫХ раундов генерации делать, если часть предложенных
# тем отсеялась как дубли и partии не хватает до requested_count. 2 —
# всего до 3 раундов (первый + 2 добора): достаточно, чтобы модель отошла
# от только что отсеянных вариантов, но не раскручивает стоимость запроса
# бесконечно, если тематика сайта реально исчерпана и модели предложить
# больше нечего.
_TOPICS_TOPUP_ROUNDS = 2


def _start_job(db, kind: str, site_id: int | None, created_by_id: int | None,
               params: dict) -> JobRun:
    job = JobRun(kind=kind, site_id=site_id, created_by_id=created_by_id,
                 params_json=params, status="running")
    db.add(job)
    db.commit()
    return job


def _finish_job(db, job: JobRun, status: str, log: str = "") -> None:
    job.status = status
    job.log_text = log
    job.finished_at = utcnow()
    db.commit()


# --- генерация тем ---

def generate_topics_sync(db, batch_id: int) -> None:
    batch = db.get(ArticleBatch, batch_id)

    # Находка №3 ревью Task 17: без этой проверки повторная постановка той же
    # задачи (сетевой ретрай брокера, дубль клика до появления защиты на
    # уровне API в Task 18) заново сходит в платную модель и ЗАНОВО добавит
    # все kept-темы как новые Article, задублировав их — ArticleBatch.articles
    # просто растёт с каждым повтором. run_batch_sync защищена по каждой
    # статье (`if article.status == "published": continue`); здесь такой же
    # природы защита нужна на уровне всей партии целиком — тихий выход, а не
    # исключение: тот же стиль, что и пропуск опубликованной статьи ниже.
    if batch.status not in _TOPICS_RUNNABLE_STATUSES:
        return

    # db.get(Site, None) не вызываем: SQLAlchemy предупреждает про поиск по
    # заведомо NULL первичному ключу ("fully NULL primary key identity cannot
    # load any object") — batch.site_id уже может быть NULL сам по себе
    # (нашли этот же случай, что и ниже), незачем ходить в БД, чтобы узнать
    # то, что уже известно из самого значения site_id.
    site = db.get(Site, batch.site_id) if batch.site_id is not None else None
    if site is None:
        # Находка №2 ревью Task 17: site_id nullable, ON DELETE SET NULL
        # (Task 14) — сайт партии могли удалить между постановкой задачи в
        # очередь и её реальным запуском. Без этой проверки следующая строка
        # (site.id) уронила бы задачу необработанным AttributeError, и
        # партия осталась бы в topics_pending навсегда — молча.
        batch.status = "failed"
        batch.error_text = "сайт этой партии удалён — генерация тем невозможна"
        db.commit()
        job = _start_job(db, "generate_topics", None, batch.created_by_id,
                         {"batch_id": batch_id, "count": batch.requested_count})
        _finish_job(db, job, "failed", batch.error_text)
        return

    job = _start_job(db, "generate_topics", site.id, batch.created_by_id,
                     {"batch_id": batch_id, "count": batch.requested_count})
    try:
        existing = [p.get("title", "") for p in
                    open_site_client(db, site).list_section_pages(site.articles_url_prefix)]
        template = resolve_prompt(db, "topics", site.id)
        text_client = build_text_client(db)

        # known_titles растёт с каждым раундом (существующие на сайте + уже
        # принятые в этой партии), поэтому следующий раунд просит модель не
        # повторять и то, что она сама только что предложила, а не только
        # то, что уже было на сайте до старта. total_proposed/dropped — для
        # честного лога, а не для решения о повторе: решение только по
        # len(kept) vs requested_count.
        known_titles = list(existing)
        kept: list[str] = []
        dropped: list[str] = []
        total_proposed = 0

        for _ in range(_TOPICS_TOPUP_ROUNDS + 1):
            remaining = batch.requested_count - len(kept)
            if remaining <= 0:
                break
            prompt = render_prompt(template, {
                "count": remaining,
                "site_name": site.name,
                "site_description": site.site_description,
                "tone_of_voice": site.tone_of_voice,
                "existing_titles": known_titles,
            })
            result = text_client.complete_json(prompt)
            if not isinstance(result.data, list):
                raise LLMError("модель вернула не массив тем")

            proposed = [str(t).strip() for t in result.data if str(t).strip()]
            total_proposed += len(proposed)
            round_kept, round_dropped = filter_duplicates(proposed, known_titles)
            kept.extend(round_kept)
            dropped.extend(round_dropped)
            known_titles.extend(round_kept)

        for topic in kept:
            db.add(Article(batch_id=batch.id, site_id=site.id, topic=topic))
        batch.status = "topics_review"
        db.commit()
        log = (f"предложено {total_proposed}, отсеяно дублей {len(dropped)}, "
               f"принято {len(kept)}")
        if len(kept) < batch.requested_count:
            log += f" из {batch.requested_count} запрошенных — тем для добора не нашлось"
        _finish_job(db, job, "ok", log)
    # Находка №1 ревью Task 17: AIConfigError (Task 13, app/ai/factory.py) не
    # входила в этот список — она моложе исходного except-списка. Без неё
    # build_text_client(db) при незаполненном ключе RouterAI (или неверном
    # ENCRYPTION_KEY) ронял всю задачу необработанным исключением, а
    # ArticleBatch/JobRun оставались в "running"/topics_pending навсегда —
    # админ не увидел бы причину нигде, кроме логов воркера. SecretDecryptionError
    # здесь ловит тот же класс ошибки конфигурации со стороны SiteClient
    # (list_section_pages), а не только RouterAI.
    except (LLMError, PromptError, SiteAPIError, SecretDecryptionError,
            AIConfigError, SoftTimeLimitExceeded) as exc:
        batch.status = "failed"
        batch.error_text = str(exc) or "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", str(exc) or "превышен лимит времени задачи")


@celery_app.task(name="app.tasks.generate_topics")
def generate_topics(batch_id: int) -> None:
    db = SessionLocal()
    try:
        generate_topics_sync(db, batch_id)
    finally:
        db.close()


# --- сборка партии ---

def run_batch_sync(db, batch_id: int) -> None:
    batch = db.get(ArticleBatch, batch_id)
    site = db.get(Site, batch.site_id) if batch.site_id is not None else None
    if site is None:
        # Находка №2 ревью Task 17: см. тот же аргумент в generate_topics_sync
        # выше. Проверка стоит до `batch.status = "running"`, чтобы партия не
        # проходила через бессмысленный промежуточный статус "running" на пути
        # к "failed", когда заранее известно, что собирать нечем.
        batch.status = "failed"
        batch.error_text = "сайт этой партии удалён — сборка статей невозможна"
        db.commit()
        job = _start_job(db, "run_batch", None, batch.created_by_id,
                         {"batch_id": batch_id, "articles": len(batch.articles)})
        _finish_job(db, job, "failed", batch.error_text)
        return

    # Находка №2 ревью Task 18: раньше здесь стояло `batch.status = "running";
    # db.commit()`. Ответственность за этот переход переехала в сам API-
    # эндпоинт (`run()`, app/api/article_batches.py) — он переводит партию в
    # "running" СИНХРОННО, в той же транзакции, что и проверка на повторный
    # запуск, до постановки задачи в очередь. Это нужно, чтобы повторный
    # (двойной) вызов run() увидел уже "running" и не поставил в очередь
    # вторую задачу run_batch на ту же партию — иначе окно гонки было равно
    # времени до реального старта задачи в Celery, а не долям миллисекунды
    # внутри одного HTTP-запроса. К моменту, когда эта функция реально
    # начинает выполняться, батч уже "running" — присваивание здесь было бы
    # переприсвоением того же значения, а не защитой; убрано как мёртвый код,
    # а не потому что было вредным (тесты test_tasks.py вызывают
    # run_batch_sync напрямую и сами выставляют status="topics_review" перед
    # вызовом — ни один не проверяет промежуточное значение "running").
    job = _start_job(db, "run_batch", site.id, batch.created_by_id,
                     {"batch_id": batch_id, "articles": len(batch.articles)})

    try:
        # Находка №1 ревью Task 17: раньше `site_client = open_site_client(...)`
        # стоял ДО try — необработанный SecretDecryptionError (токен сайта
        # расшифрован другим ENCRYPTION_KEY) ронял задачу, а партия оставалась
        # в "running" навсегда. Перенесено внутрь try вместе с циклом.
        site_client = open_site_client(db, site)
        for article in batch.articles:
            if article.status == "published":
                continue
            # Падение одной статьи не должно отменять остальные: билдер сам
            # пишет причину в error_text и оставляет статью в failed.
            build_for(db, article, site, site_client, job.id)
            db.commit()
    except SoftTimeLimitExceeded:
        # Лимит вычисляется от числа статей (см. Task 18), так что сюда мы
        # попадаем только при реально зависшей партии. Уже опубликованные
        # статьи остаются опубликованными — их пропустит `continue` при
        # повторном запуске; помечаем партию, чтобы она не висела в "running".
        done = len([a for a in batch.articles if a.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"превышен лимит времени партии, готово "
                            f"{done}/{len(batch.articles)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        # Находка №1 ревью Task 17: AIConfigError долетает сюда либо из
        # open_site_client (SecretDecryptionError) выше, либо изнутри
        # build_for → build_for() (Task 16, app/articles/builder.py) собирает
        # клиентов RouterAI ДО входа в собственный try — см. её докстринг.
        # Это ошибка конфигурации панели, ОДНА И ТА ЖЕ для всех статей партии
        # (ключ либо задан, либо нет), а не отказ, специфичный для конкретной
        # статьи — поэтому она обрывает партию целиком, а не просто эту
        # статью, в отличие от LLMError/ImageError/SiteAPIError, с которыми
        # build_for() справляется сам и никогда их наружу не отдаёт.
        done = len([a for a in batch.articles if a.status == "published"])
        batch.status = "failed"
        batch.error_text = f"{exc}; готово {done}/{len(batch.articles)}"
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return

    batch.status = "done"
    db.commit()
    failed = [a for a in batch.articles if a.status == "failed"]
    _finish_job(db, job, "ok" if not failed else "failed",
                f"готово {len(batch.articles) - len(failed)}/{len(batch.articles)}")


@celery_app.task(name="app.tasks.run_batch")
def run_batch(batch_id: int) -> None:
    db = SessionLocal()
    try:
        run_batch_sync(db, batch_id)
    finally:
        db.close()


# --- повтор одной статьи ---

def retry_article_sync(db, article_id: int) -> None:
    article = db.get(Article, article_id)
    site = db.get(Site, article.site_id) if article.site_id is not None else None
    if site is None:
        # Находка №2 ревью Task 17: та же ситуация, что и у партии, — сайт
        # статьи мог быть удалён между постановкой задачи и её запуском.
        article.status = "failed"
        article.error_text = "сайт этой статьи удалён — повтор невозможен"
        db.commit()
        job = _start_job(db, "retry_article", None, None, {"article_id": article_id})
        _finish_job(db, job, "failed", article.error_text)
        return

    job = _start_job(db, "retry_article", site.id, None, {"article_id": article_id})
    try:
        build_for(db, article, site, open_site_client(db, site), job.id)
    except SoftTimeLimitExceeded:
        article.status = "failed"
        article.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        # Находка №1 ревью Task 17: см. подробный комментарий в run_batch_sync
        # выше — тот же класс ошибки, тот же непойманный путь без этого except.
        article.status = "failed"
        article.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return

    db.commit()
    _finish_job(db, job, "ok" if article.status == "published" else "failed",
                article.error_text)


@celery_app.task(name="app.tasks.retry_article")
def retry_article(article_id: int) -> None:
    db = SessionLocal()
    try:
        retry_article_sync(db, article_id)
    finally:
        db.close()


# --- строители: сборка партии ---

def run_company_batch_sync(db, batch_id: int) -> None:
    batch = db.get(CompanyBatch, batch_id)
    site = db.get(Site, batch.site_id) if batch.site_id is not None else None
    if site is None:
        # Находка №2 ревью Task 17 (тот же случай, что и у ArticleBatch): сайт
        # партии мог быть удалён между постановкой задачи и её реальным
        # запуском — site_id nullable, ON DELETE SET NULL.
        batch.status = "failed"
        batch.error_text = "сайт этой партии удалён — сборка компаний невозможна"
        db.commit()
        job = _start_job(db, "run_company_batch", None, batch.created_by_id,
                         {"batch_id": batch_id, "companies": len(batch.companies)})
        _finish_job(db, job, "failed", batch.error_text)
        return

    job = _start_job(db, "run_company_batch", site.id, batch.created_by_id,
                     {"batch_id": batch_id, "companies": len(batch.companies)})
    try:
        site_client = open_site_client(db, site)
        for company in batch.companies:
            if company.status == "published":
                continue
            # Падение одной компании не должно отменять остальные: билдер сам
            # пишет причину в error_text и оставляет компанию в failed.
            build_for_company(db, company, site, site_client, job.id)
            db.commit()
    except SoftTimeLimitExceeded:
        done = len([c for c in batch.companies if c.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"превышен лимит времени партии, готово "
                            f"{done}/{len(batch.companies)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        # Ошибка конфигурации панели (RouterAI не настроен, или токен сайта
        # расшифрован другим ключом) — одна и та же для всех компаний партии,
        # обрывает партию целиком, а не только текущую компанию, в отличие от
        # ScrapeError/LLMError/PromptError/SiteAPIError, с которыми builder.py
        # справляется сам и никогда их наружу не отдаёт.
        done = len([c for c in batch.companies if c.status == "published"])
        batch.status = "failed"
        batch.error_text = f"{exc}; готово {done}/{len(batch.companies)}"
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        return

    batch.status = "done"
    db.commit()
    failed = [c for c in batch.companies if c.status == "failed"]
    _finish_job(db, job, "ok" if not failed else "failed",
               f"готово {len(batch.companies) - len(failed)}/{len(batch.companies)}")


@celery_app.task(name="app.tasks.run_company_batch")
def run_company_batch(batch_id: int) -> None:
    db = SessionLocal()
    try:
        run_company_batch_sync(db, batch_id)
    finally:
        db.close()


# --- строители: повтор одной компании ---

def retry_company_sync(db, company_id: int) -> None:
    company = db.get(Company, company_id)
    site = db.get(Site, company.site_id) if company.site_id is not None else None
    if site is None:
        company.status = "failed"
        company.error_text = "сайт этой компании удалён — повтор невозможен"
        db.commit()
        job = _start_job(db, "retry_company", None, None, {"company_id": company_id})
        _finish_job(db, job, "failed", company.error_text)
        return

    job = _start_job(db, "retry_company", site.id, None, {"company_id": company_id})
    try:
        build_for_company(db, company, site, open_site_client(db, site), job.id)
    except SoftTimeLimitExceeded:
        company.status = "failed"
        company.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", company.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        company.status = "failed"
        company.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return

    db.commit()
    _finish_job(db, job, "ok" if company.status == "published" else "failed",
               company.error_text)


@celery_app.task(name="app.tasks.retry_company")
def retry_company(company_id: int) -> None:
    db = SessionLocal()
    try:
        retry_company_sync(db, company_id)
    finally:
        db.close()
