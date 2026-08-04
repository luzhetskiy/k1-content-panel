"""Быстрая проверка API сайта — создаём тестовую страницу-черновик."""

import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

SITE = "https://stroybaza-samara.ru"
DOMAIN = "stroybaza-samara.ru"
API_URL = f"{SITE}/api/v1/staticpages/"

token_key = f"SITE_API_TOKEN_{DOMAIN.removesuffix('.ru')}"
parent_key = f"SITE_PARENT_ID_{DOMAIN.removesuffix('.ru')}"

TOKEN = os.getenv(token_key)
PARENT = int(os.getenv(parent_key, "0"))

if not TOKEN:
    print(f"ERROR: {token_key} не найден в .env")
    sys.exit(1)

payload = {
    "title": "API Test — k1-parser",
    "url": "/s/api-test-k1-parser",
    "text": "<p>Тестовая страница для проверки API. Можно удалить.</p>",
    "published": False,
    "meta_keywords": "тест, api, k1-parser",
    "meta_description": "Тестовая страница для проверки подключения к API.",
    "wide_view": True,
    "use_editor": False,
    "parent": PARENT,
}

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Token {TOKEN}",
}

print(f"POST {API_URL}")
print(f"Parent: {PARENT}")
print(f"Token: {TOKEN[:10]}...")
print("-" * 60)

resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
print(f"HTTP {resp.status_code}")
try:
    body = resp.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
except Exception:
    print(resp.text[:1000])
