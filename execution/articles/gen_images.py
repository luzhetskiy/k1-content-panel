"""
Генерация иллюстраций для статей через агрегатор RouterAI.

Модель gpt-image-2 отдаёт только фиксированные размеры (1024x1024, 1536x1024,
1024x1536) и игнорирует aspect_ratio — нужные пропорции получаем кропом по центру.
Результат сохраняется в webp с оптимизацией по размеру файла.

Запуск:
    python execution/articles/gen_images.py                      # все из манифеста
    python execution/articles/gen_images.py --only article_1-1   # выборочно
    python execution/articles/gen_images.py --workers 4 --force
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

API_URL = "https://routerai.ru/api/v1/images"
MODEL = os.getenv("ROUTERAI_IMAGE_MODEL", "openai/gpt-image-2")
MANIFEST = Path(__file__).parent / "images_manifest.json"
OUT_DIR = Path(__file__).resolve().parents[2] / "articles_batch_1" / "images"
MAX_WIDTH = 1600
WEBP_QUALITY = 82
TIMEOUT = 420
RETRIES = 3


def crop_to_ratio(im: Image.Image, ratio: str) -> Image.Image:
    """Центральный кроп до заданного соотношения сторон ('21:9')."""
    rw, rh = (int(x) for x in ratio.split(":"))
    target = rw / rh
    w, h = im.size
    if w / h > target:
        new_w = int(h * target)
        left = (w - new_w) // 2
        return im.crop((left, 0, left + new_w, h))
    new_h = int(w / target)
    top = (h - new_h) // 2
    return im.crop((0, top, w, top + new_h))


def optimize(raw: bytes, crop: str | None, out_path: Path) -> tuple[tuple[int, int], int]:
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if crop:
        im = crop_to_ratio(im, crop)
    if im.width > MAX_WIDTH:
        im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
    im.save(out_path, "WEBP", quality=WEBP_QUALITY, method=6)
    return im.size, out_path.stat().st_size


def generate(item: dict, base_style: str, api_key: str) -> dict:
    name = item["name"]
    out_path = OUT_DIR / f"{name}.webp"
    prompt = base_style.format(ratio=item["ratio"]) + " Сюжет: " + item["scene"]

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "n": 1,
        "size": item["size"],
        "quality": item["quality"],
        "output_format": "webp",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            started = time.time()
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=TIMEOUT)
            if not resp.ok:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                time.sleep(5 * attempt)
                continue
            body = resp.json()
            raw = base64.b64decode(body["data"][0]["b64_json"])
            size, nbytes = optimize(raw, item.get("crop"), out_path)
            return {
                "name": name, "ok": True,
                "size": f"{size[0]}x{size[1]}", "kb": nbytes // 1024,
                "cost": body.get("usage", {}).get("cost"),
                "secs": round(time.time() - started),
            }
        except Exception as e:
            last_err = str(e)[:200]
            time.sleep(5 * attempt)

    return {"name": name, "ok": False, "error": last_err}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="Имена картинок из манифеста")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="Перегенерировать уже существующие")
    args = parser.parse_args()

    api_key = os.getenv("ROUTERAI_API_KEY")
    if not api_key:
        print("ERROR: не задан ROUTERAI_API_KEY в .env")
        sys.exit(1)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = manifest["images"]
    if args.only:
        items = [i for i in items if i["name"] in args.only]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.force:
        items = [i for i in items if not (OUT_DIR / f"{i['name']}.webp").exists()]

    if not items:
        print("Нечего генерировать — все файлы уже на месте.")
        return

    print(f"К генерации: {len(items)} картинок, модель {MODEL}, потоков {args.workers}")
    print("-" * 70)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(generate, i, manifest["base_style"], api_key): i for i in items}
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            if res["ok"]:
                print(f"  OK   {res['name']}  {res['size']}  {res['kb']} КБ  "
                      f"{res['secs']}с  cost={res['cost']}")
            else:
                print(f"  FAIL {res['name']}  {res['error']}")

    ok = [r for r in results if r["ok"]]
    total_cost = sum(r["cost"] or 0 for r in ok)
    print("-" * 70)
    print(f"Готово: {len(ok)}/{len(results)}  •  суммарная стоимость: {total_cost:.2f}")
    print(f"Папка: {OUT_DIR}")
    failed = [r["name"] for r in results if not r["ok"]]
    if failed:
        print(f"Не сгенерированы: {', '.join(failed)}")


if __name__ == "__main__":
    main()
