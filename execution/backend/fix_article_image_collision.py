"""Разовая починка статей, задетых коллизией имён файлов (см. builder.py,
image_filename): контентные картинки этих статей были залиты под именами
article_{id}-N.webp, которые уже занимал старый CLI-пайплайн на том же
сайте, и filemanager молча перезаписал их (или их перезаписали позже) —
статья показывает чужие картинки.

Промпты уже сохранены в article_images.prompt при первой сборке — этот
скрипт их переиспользует (просит у RouterAI только новую картинку, не
новый текст и не новый промпт), заливает под новым неймспейсом (cp-article-,
уже не пересекается со старой схемой) и правит <img src> в теле статьи как
в БД, так и на самом сайте (PATCH text страницы).

Запуск:
    docker compose -f docker-compose.prod.yml --env-file .env.prod \
        exec api python fix_article_image_collision.py 4 5 6
    # добавить --dry-run, чтобы только посмотреть, что будет сделано,
    # без платных вызовов RouterAI и без записи на сайт/в БД
"""

import argparse
import sys

from app.ai.factory import build_image_generator, image_params
from app.ai.images import ImageError
from app.ai.watermark import apply_watermark
from app.api.admin_sites import open_client as open_site_client
from app.articles.builder import CONTENT_CROP, image_filename
from app.db import SessionLocal
from app.models.article import Article, ArticleImage
from app.models.job import JobRun, LlmUsage
from app.models.site import Site
from app.sites.client import ARTICLE_IMG_DIR, SiteAPIError


def fix_article(db, article: Article, site: Site, image_generator, site_client,
                 params: dict, watermark_bytes: bytes, job: JobRun, dry_run: bool) -> None:
    content_images = [i for i in article.images if i.kind == "content"]
    content_images.sort(key=lambda i: i.position)
    if not content_images:
        print(f"  статья {article.id} «{article.title}» — нет контентных картинок, пропуск")
        return

    replacements: list[tuple[str, str]] = []
    total_cost = 0.0
    print(f"  статья {article.id} «{article.title}» — {len(content_images)} картинок")

    for image in content_images:
        new_filename = image_filename(article.id, image.position)
        new_path = f"/media/{ARTICLE_IMG_DIR}{new_filename}"
        if image.remote_path == new_path:
            print(f"    позиция {image.position}: уже на новом имени, пропуск")
            continue
        if dry_run:
            print(f"    позиция {image.position}: {image.remote_path} -> {new_path} (dry-run)")
            continue

        result = image_generator.generate(
            prompt=image.prompt, size=params["size"], quality=params["quality"],
            crop=CONTENT_CROP)
        data = apply_watermark(result.data, watermark_bytes)
        uploaded_path = site_client.upload_file(data, new_filename, ARTICLE_IMG_DIR)
        assert uploaded_path == new_path, f"{uploaded_path} != {new_path}"

        replacements.append((image.remote_path, new_path))
        image.remote_path = new_path
        total_cost += result.cost
        db.add(LlmUsage(job_run_id=job.id, kind="image", model=image_generator.model,
                        tokens_prompt=0, tokens_completion=0, cost=result.cost))
        print(f"    позиция {image.position}: перегенерирована, "
              f"{result.size[0]}x{result.size[1]}, cost={result.cost}")

    if dry_run or not replacements:
        return

    new_html = article.body_html
    for old_path, new_path in replacements:
        new_html = new_html.replace(old_path, new_path)
    article.body_html = new_html

    if article.remote_page_id:
        site_client.update_page_text(article.remote_page_id, new_html)
        print(f"    страница {article.remote_page_id} обновлена на сайте")
    else:
        print("    ВНИМАНИЕ: remote_page_id пуст — страница на сайте не обновлена")

    db.commit()
    print(f"  готово, потрачено {total_cost:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("article_ids", nargs="+", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    job = JobRun(kind="fix_image_collision", status="running",
                 params_json={"article_ids": args.article_ids, "dry_run": args.dry_run})
    if not args.dry_run:
        db.add(job)
        db.commit()

    failed = False
    try:
        for article_id in args.article_ids:
            article = db.get(Article, article_id)
            if article is None:
                print(f"статья {article_id} не найдена")
                failed = True
                continue
            site = db.get(Site, article.site_id) if article.site_id else None
            if site is None:
                print(f"статья {article_id}: сайт не найден")
                failed = True
                continue
            if job.site_id is None:
                job.site_id = site.id

            watermark_bytes = b""
            if site.watermark_path:
                try:
                    with open(site.watermark_path, "rb") as f:
                        watermark_bytes = f.read()
                except OSError:
                    pass

            try:
                fix_article(db, article, site, build_image_generator(db),
                           open_site_client(db, site), image_params(db),
                           watermark_bytes, job, args.dry_run)
            except (ImageError, SiteAPIError) as exc:
                print(f"  ОШИБКА на статье {article_id}: {exc}")
                db.rollback()
                failed = True
    finally:
        if not args.dry_run:
            job.status = "failed" if failed else "ok"
            db.commit()
        db.close()

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
