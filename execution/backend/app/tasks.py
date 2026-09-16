"""Фоновые задачи. Каждая обёрнута парой sync-функций: сама задача открывает
сессию, а логика живёт в `*_sync(db, ...)` — так её можно тестировать без брокера.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import current_task
from celery.exceptions import SoftTimeLimitExceeded

from app.ai.factory import AIConfigError, build_text_client
from app.ai.prompts import PromptError, render_prompt, resolve_prompt
from app.ai.text import LLMError
from app.api.admin_sites import open_client as open_site_client
from app.articles.builder import build_for, regenerate_article_for
from app.articles.topics import filter_duplicates
from app.category_meta.generator import MetaValidationError, generate_category
from app.category_meta.runs import active_run, finish_run_if_complete, next_queued
from app.category_meta.seeds import SeedsError, generate_seeds, needs_seed
from app.category_meta.tree import sync_categories
from app.celery_app import celery_app
from app.clock import utcnow
from app.companies.builder import build_for as build_for_company
from app.db import SessionLocal
from app.models.article import Article, ArticleBatch
from app.models.company import Company, CompanyBatch
from app.models.category_meta import CategoryMeta, MetaRun
from app.models.job import JobRun, LlmUsage
from app.models.site import Site
from app.settings.crypto import SecretDecryptionError
from app.sites.client import SiteAPIError
from app.wordstat.client import WordstatAuthError, WordstatError
from app.wordstat.factory import (
    WordstatConfigError, build_wordstat_client, hourly_limit, stoplist,
)
from app.wordstat.quota import QuotaExceeded

logger = logging.getLogger(__name__)

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
    # celery_task_id существует в модели с Task 14, но до 2026-09-13 никогда не
    # заполнялся — по строке журнала нельзя было найти задачу в логах воркера,
    # а именно это понадобилось при разборе партии 25. Логика видимости
    # зависших (batch_runtime_state) на него НЕ опирается: у Redis-бэкенда
    # результат живёт сутки, после чего AsyncResult отдаёт PENDING и для
    # мёртвой задачи, и для стоящей в очереди. Поле — для человека с логами.
    # current_task пуст, когда *_sync вызвана напрямую (тесты) — тогда пустая
    # строка, как и раньше.
    task = current_task
    job = JobRun(kind=kind, site_id=site_id, created_by_id=created_by_id,
                 params_json=params, status="running",
                 celery_task_id=(task.request.id or "") if task else "")
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
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        # Белый список выше не покрывает непредусмотренное: без этой ветки
        # партия осталась бы в topics_pending навсегда, а джоба — в running.
        db.rollback()
        batch.status = "failed"
        batch.error_text = f"непредвиденная ошибка: {type(exc).__name__}: {exc}"
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        raise


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
            try:
                build_for(db, article, site, site_client, job.id)
            except (SoftTimeLimitExceeded, AIConfigError, SecretDecryptionError):
                # Лимит времени и ошибка конфигурации — общие для ВСЕЙ партии
                # (ключ либо задан, либо нет; время либо вышло, либо нет), их
                # обрабатывают внешние except ниже и обрывают партию целиком.
                # Перевыброс, а не обработка здесь: семантика, описанная в их
                # комментариях, не меняется.
                raise
            except Exception as exc:  # noqa: BLE001 — см. ниже, это и есть барьер
                # Билдер сам ловит свои типы (LLMError/ImageError/SiteAPIError/
                # PromptError/ArticleBuildError) и наружу их не отдаёт, поэтому
                # сюда попадает только то, чего никто не предусмотрел. До
                # 2026-09-12 такое исключение роняло задачу целиком: партия 25
                # потеряла 23 статьи из-за одного необёрнутого
                # requests.ConnectionError (отказ DNS по домену сайта). Тот
                # конкретный тип закрыт обёрткой в SiteClient._send, но белый
                # список по определению не покрывает следующий такой же случай —
                # этот барьер покрывает весь класс.
                # rollback обязателен: транзакция могла остаться незавершённой
                # (тот же довод, что в ArticleBuilder.build()). Безусловный
                # rollback на чистой сессии безопасен — no-op.
                db.rollback()
                article.status = "failed"
                article.error_text = (f"непредвиденная ошибка: "
                                      f"{type(exc).__name__}: {exc}")
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
    except Exception as exc:  # noqa: BLE001 — барьер согласованности состояния
        # Сбой ВНЕ цикла по статьям: open_site_client, обращение к
        # batch.articles на оборвавшемся соединении с БД и прочее
        # непредусмотренное. Внутрицикловый барьер выше сюда не пускает отказы
        # отдельных статей, поэтому этот except означает «партию продолжать
        # нечем», а не «одна статья не вышла».
        #
        # Порядок важен: этот except обязан оставаться ПОСЛЕДНИМ —
        # SoftTimeLimitExceeded и AIConfigError/SecretDecryptionError его
        # подклассы, и перестановка тихо отключила бы уже работающую и
        # покрытую тестами обработку (см.
        # test_run_batch_unexpected_exception_does_not_swallow_soft_time_limit).
        db.rollback()
        done = len([a for a in batch.articles if a.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"непредвиденная ошибка: {type(exc).__name__}: {exc}; "
                            f"готово {done}/{len(batch.articles)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        # raise — отличие от соседних обработчиков, и оно осознанное: таймаут и
        # ошибка конфигурации ожидаемы и полностью описаны текстом в UI, а
        # непредвиденное исключение нужно видеть трейсбеком в логах воркера,
        # и задача в Celery должна быть FAILURE, а не SUCCESS. В БД к этому
        # моменту уже всё согласовано.
        raise

    batch.status = "done"
    failed = [a for a in batch.articles if a.status == "failed"]
    # error_text — поле «что не так СЕЙЧАС», а не журнал прошлых обрывов:
    # BatchPage.tsx рисует его безусловным красным алертом. Найдено на партии 25
    # (2026-09-12): после успешной досборки 49/49 статус стал done, а текст
    # остался с обрыва 3 сентября («готово 25/49») — полностью собранная партия
    # показывалась менеджеру как упавшая. Поэтому на завершении текст всегда
    # перезаписывается актуальным, а не дописывается и не сохраняется:
    # пусто, если собралось всё. ArticleBuilder.build() со своим
    # article.error_text поступает точно так же.
    batch.error_text = (f"{len(failed)} из {len(batch.articles)} статей не собрались — "
                        f"причины в таблице" if failed else "")
    db.commit()
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
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        db.rollback()
        article.status = "failed"
        article.error_text = f"непредвиденная ошибка: {type(exc).__name__}: {exc}"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        raise

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


# --- перегенерация опубликованной статьи (текст/картинки/обложка) ---

def regenerate_article_sync(db, article_id: int, *, text: bool, images: bool,
                            cover: bool) -> None:
    """В отличие от retry_article_sync, ни одна ветка здесь НЕ трогает
    article.status — статья уже опубликована, её страница на сайте
    продолжает существовать и работать независимо от исхода этого раунда.
    Отказ отражается только в regenerating/error_text. Это намеренное
    расхождение с соседней retry_article_sync (которая как раз обязана
    переводить статью в "failed"), а не пропуск — не «чинить» по аналогии
    с ней."""
    article = db.get(Article, article_id)
    if article.status != "published":
        # Гонка с эндпоинтом (app/api/article_batches.py, regenerate): он
        # уже отклоняет неопубликованные статьи синхронно, сюда можно
        # попасть только если статус успел измениться между постановкой
        # задачи и её реальным стартом. Тихий выход, тот же стиль, что и у
        # generate_topics_sync при повторной постановке той же задачи.
        article.regenerating = False
        db.commit()
        return

    site = db.get(Site, article.site_id) if article.site_id is not None else None
    if site is None:
        article.regenerating = False
        article.error_text = "сайт этой статьи удалён — перегенерация невозможна"
        db.commit()
        job = _start_job(db, "regenerate_article", None, None, {"article_id": article_id})
        _finish_job(db, job, "failed", article.error_text)
        return

    job = _start_job(db, "regenerate_article", site.id, None, {"article_id": article_id})
    try:
        regenerate_article_for(db, article, site, open_site_client(db, site), job.id,
                               text=text, images=images, cover=cover)
    except SoftTimeLimitExceeded:
        article.regenerating = False
        article.error_text = "превышен лимит времени задачи"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        return
    except (AIConfigError, SecretDecryptionError) as exc:
        article.regenerating = False
        article.error_text = str(exc)
        db.commit()
        _finish_job(db, job, "failed", str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        # article.status НЕ трогаем — это запрещено докстрингом функции выше:
        # страница на сайте продолжает существовать независимо от исхода
        # раунда перегенерации. Снимается только флаг и пишется причина.
        db.rollback()
        article.regenerating = False
        article.error_text = f"непредвиденная ошибка: {type(exc).__name__}: {exc}"
        db.commit()
        _finish_job(db, job, "failed", article.error_text)
        raise

    # Подстраховка: ArticleBuilder.regenerate() сама снимает этот флаг по
    # завершении, но обёртка не должна полагаться на то, что он снят именно
    # билдером — иначе тест, подменяющий regenerate_article_for целиком (без
    # реального билдера), не может проверить, что флаг снимается, а сама
    # обёртка перестаёт быть источником истины о собственном состоянии.
    article.regenerating = False
    db.commit()
    _finish_job(db, job, "ok" if not article.error_text else "failed", article.error_text)


@celery_app.task(name="app.tasks.regenerate_article")
def regenerate_article(article_id: int, *, text: bool, images: bool, cover: bool) -> None:
    db = SessionLocal()
    try:
        regenerate_article_sync(db, article_id, text=text, images=images, cover=cover)
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
            try:
                build_for_company(db, company, site, site_client, job.id)
            except (SoftTimeLimitExceeded, AIConfigError, SecretDecryptionError):
                raise       # общие для всей партии — см. run_batch_sync выше
            except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
                db.rollback()
                company.status = "failed"
                company.error_text = (f"непредвиденная ошибка: "
                                      f"{type(exc).__name__}: {exc}")
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
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        db.rollback()
        done = len([c for c in batch.companies if c.status == "published"])
        batch.status = "failed"
        batch.error_text = (f"непредвиденная ошибка: {type(exc).__name__}: {exc}; "
                            f"готово {done}/{len(batch.companies)}")
        db.commit()
        _finish_job(db, job, "failed", batch.error_text)
        raise

    batch.status = "done"
    failed = [c for c in batch.companies if c.status == "failed"]
    # Тот же довод, что и у партии статей выше: error_text описывает текущее
    # состояние, а не прошлое.
    batch.error_text = (f"{len(failed)} из {len(batch.companies)} компаний не собрались — "
                        f"причины в таблице" if failed else "")
    db.commit()
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
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        # Именно так 2026-09-04 повисла компания 292: отказ DNS по
        # stroybaza-kaluga.ru оставил её в "generating", а джобу 183 в
        # "running" — на восемь дней.
        db.rollback()
        company.status = "failed"
        company.error_text = f"непредвиденная ошибка: {type(exc).__name__}: {exc}"
        db.commit()
        _finish_job(db, job, "failed", company.error_text)
        raise

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


# --- метатеги категорий (directions/2026-09-16-category-meta-design.md) ---
#
# Цепочка: запуск ставит в Celery только первую категорию, каждая задача по
# завершении — следующую. Проект занимает не больше одного слота воркера из двух.

# Худший случай категории: Wordstat 3 запроса × (30 с × 3 попытки + паузы 2+4)
# = 288 с; LLM 2 попытки × 366 с = 732 с; запись на сайт 3 попытки ×
# (список метатегов 120 с + запись 60 с) + паузы 1+2 = 543 с. Итого ≈ 1563 с.
CATEGORY_SOFT_LIMIT = 1600
CATEGORY_HARD_LIMIT = 1780
# Подготовка запуска: страницы категорий и sitemap (~360 с) + фразы LLM пачками
# по 60 категорий (366 с на пачку). 2400 с хватает на ~250 категорий.
RUN_START_SOFT_LIMIT = 2400
RUN_START_HARD_LIMIT = 2580
# У Redis-брокера visibility_timeout — час: задача с ETA дольше него
# доставляется повторно. Поэтому ждём квоту кусками не больше 10 минут.
MAX_QUOTA_COUNTDOWN_SECONDS = 600

SEED_MISSING_TEXT = ("модель не вернула поисковую фразу для категории — "
                     "запустите обновление ещё раз")


def _record_usage(db, job_run_id: int, model: str, tokens_prompt: int,
                  tokens_completion: int, cost: float) -> None:
    db.add(LlmUsage(job_run_id=job_run_id, kind="text", model=model,
                    tokens_prompt=tokens_prompt, tokens_completion=tokens_completion, cost=cost))
    db.commit()


def _close_run_job(db, run: MetaRun) -> None:
    job = db.get(JobRun, run.job_run_id) if run.job_run_id else None
    if job is None or job.finished_at is not None:
        return
    if run.error_text:
        _finish_job(db, job, "failed", run.error_text)
        return
    failed = db.query(CategoryMeta).filter(CategoryMeta.site_id == run.site_id,
                                           CategoryMeta.status == "failed").count()
    done = db.query(CategoryMeta).filter(CategoryMeta.site_id == run.site_id,
                                         CategoryMeta.status == "done").count()
    _finish_job(db, job, "ok" if not failed else "failed",
                f"готово {done}/{run.total}, ошибок {failed}")


def _complete_run(db, site_id: int) -> None:
    run = finish_run_if_complete(db, site_id)
    if run is not None:
        _close_run_job(db, run)


def _fail_run(db, run: MetaRun, text: str) -> None:
    run.error_text = text
    run.finished_at = utcnow()
    db.commit()
    _close_run_job(db, run)


def start_meta_run_sync(db, run_id: int) -> int | None:
    """Синхронизация дерева и фразы; возвращает id первой категории для цепочки."""
    run = db.get(MetaRun, run_id)
    site = db.get(Site, run.site_id)
    job = _start_job(db, "category_meta_run", site.id, run.created_by_id, {"run_id": run_id})
    run.job_run_id = job.id
    db.commit()
    try:
        synced = sync_categories(db, site, open_site_client(db, site))
        pending = [row for row in synced.active if needs_seed(row)]
        missing_ids: set[int] = set()
        if pending:
            text_client = build_text_client(db)
            missing = generate_seeds(
                db, site, pending, text_client,
                lambda tp, tc, cost: _record_usage(db, job.id, text_client.model, tp, tc, cost))
            missing_ids = {row.id for row in missing}
        for row in synced.active:
            row.wait_until = None
            row.started_at = None
            row.updated_at = utcnow()
            if row.id in missing_ids or not row.seed_phrase:
                row.status, row.error_text = "failed", SEED_MISSING_TEXT
            else:
                row.status, row.error_text = "queued", ""
        run.total = len(synced.active)
        db.commit()
    except SoftTimeLimitExceeded:
        db.rollback()
        _fail_run(db, run, "превышен лимит времени подготовки запуска")
        return None
    except (SiteAPIError, LLMError, PromptError, SeedsError, AIConfigError,
            SecretDecryptionError) as exc:
        db.rollback()
        _fail_run(db, run, str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 — барьер, см. run_batch_sync
        db.rollback()
        _fail_run(db, run, f"непредвиденная ошибка: {type(exc).__name__}: {exc}")
        raise

    first = next_queued(db, site.id)
    if first is None:
        _complete_run(db, site.id)
        return None
    return first.id


def _fail_category(db, category: CategoryMeta, text: str) -> None:
    category.status = "failed"
    category.error_text = text
    category.wait_until = None
    category.updated_at = utcnow()
    db.commit()


def _fail_queued(db, site_id: int, text: str) -> None:
    """Ошибка конфигурации (ключ Wordstat, RouterAI) — одна на весь запуск:
    остальные категории в очереди получают тот же текст, запуск закрывается."""
    for row in db.query(CategoryMeta).filter(CategoryMeta.site_id == site_id,
                                             CategoryMeta.status == "queued").all():
        row.status, row.error_text, row.wait_until = "failed", text, None
        row.updated_at = utcnow()
    db.commit()


def generate_category_meta_sync(db, category_id: int,
                                continue_run: bool = True) -> tuple[float | None, int | None]:
    """Возвращает (countdown для повтора этой же задачи, id следующей категории цепочки)."""
    category = db.get(CategoryMeta, category_id)
    if category is None:
        return None, None
    site = db.get(Site, category.site_id)
    if category.status != "queued":
        # Категорию уже взяла другая задача (перегенерация или вторая цепочка
        # после перезапуска) — не обрабатываем дважды, но цепочку не рвём.
        if not continue_run:
            return None, None
        following = next_queued(db, site.id)
        return None, (following.id if following and following.id != category_id else None)

    run = active_run(db, site.id) if continue_run else None
    own_job: JobRun | None = None

    def job_id() -> int:
        nonlocal own_job
        if run is not None and run.job_run_id is not None:
            return run.job_run_id
        if own_job is None:
            own_job = _start_job(db, "category_meta", site.id, None, {"category_id": category_id})
        return own_job.id

    category.status = "in_work"
    category.started_at = utcnow()
    category.updated_at = utcnow()
    db.commit()

    stop_run_text = ""
    try:
        text_client = build_text_client(db)
        generate_category(
            db, category, site,
            wordstat_factory=lambda: build_wordstat_client(db),
            text_client=text_client, site_client=open_site_client(db, site),
            limit=hourly_limit(db), stoplist=stoplist(db),
            record_usage=lambda tp, tc, cost: _record_usage(db, job_id(), text_client.model,
                                                            tp, tc, cost))
    except QuotaExceeded as wait:
        db.rollback()
        category.status = "queued"
        category.wait_until = utcnow() + timedelta(seconds=wait.seconds)
        category.updated_at = utcnow()
        db.commit()
        return min(wait.seconds, MAX_QUOTA_COUNTDOWN_SECONDS), None
    except SoftTimeLimitExceeded:
        db.rollback()
        _fail_category(db, category, "превышен лимит времени задачи")
    except (WordstatAuthError, WordstatConfigError, AIConfigError, SecretDecryptionError) as exc:
        db.rollback()
        _fail_category(db, category, str(exc))
        stop_run_text = str(exc)
    except (WordstatError, LLMError, PromptError, SiteAPIError, MetaValidationError,
            SeedsError) as exc:
        db.rollback()
        _fail_category(db, category, str(exc))
    except Exception as exc:  # noqa: BLE001 — барьер: цепочка не должна рваться
        logger.exception("метатеги категории %s: непредвиденная ошибка", category_id)
        db.rollback()
        _fail_category(db, category, f"непредвиденная ошибка: {type(exc).__name__}: {exc}")

    if own_job is not None:
        _finish_job(db, own_job, "ok" if category.status == "done" else "failed",
                    category.error_text)
    if stop_run_text:
        _fail_queued(db, site.id, stop_run_text)
    _complete_run(db, site.id)
    if not continue_run:
        return None, None
    following = next_queued(db, site.id)
    return None, (following.id if following else None)


def enqueue_category_meta(category_id: int, *, continue_run: bool = True,
                          countdown: float = 0) -> None:
    generate_category_meta.apply_async(
        args=[category_id], kwargs={"continue_run": continue_run}, countdown=countdown,
        soft_time_limit=CATEGORY_SOFT_LIMIT, time_limit=CATEGORY_HARD_LIMIT)


def enqueue_meta_run(run_id: int) -> None:
    start_meta_run.apply_async(args=[run_id], soft_time_limit=RUN_START_SOFT_LIMIT,
                               time_limit=RUN_START_HARD_LIMIT)


@celery_app.task(name="app.tasks.start_meta_run")
def start_meta_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        first = start_meta_run_sync(db, run_id)
    finally:
        db.close()
    if first is not None:
        enqueue_category_meta(first)


@celery_app.task(name="app.tasks.generate_category_meta")
def generate_category_meta(category_id: int, continue_run: bool = True) -> None:
    db = SessionLocal()
    try:
        countdown, following = generate_category_meta_sync(db, category_id, continue_run)
    finally:
        db.close()
    if countdown:
        enqueue_category_meta(category_id, continue_run=continue_run, countdown=countdown)
    elif following is not None:
        enqueue_category_meta(following)
