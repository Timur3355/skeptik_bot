import requests
import time
import schedule
from datetime import datetime
import os
import threading
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys
import io
import traceback
import json
import random

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API_PROVIDER = os.getenv("API_PROVIDER", "openai").lower()
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-r1")

# Список тем для разнообразия
TOPICS = [
    "логистические провалы Ozon: затраты, сроки доставки, убытки",
    "штрафы и возвраты Wildberries: как компания зарабатывает на продавцах",
    "долговая нагрузка Магнита: кредиты, проценты, соотношение долга к EBITDA",
    "маркетинговые расходы Ozon: сколько тратят на привлечение клиентов и окупается ли это",
    "технологические проблемы Wildberries: баги, сбои, инвестиции в IT",
    "стратегия экспансии Магнита: открытие и закрытие магазинов, эффективность"
]

PROVIDER_CONFIG = {
    "openai": {
        "url": "https://api.chatanywhere.tech/v1/chat/completions",
        "default_model": "deepseek-r1",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
    },
}

config = PROVIDER_CONFIG.get(API_PROVIDER, PROVIDER_CONFIG["openai"])
API_URL = config["url"]
API_HEADERS_FUNC = config["headers"]
API_DEFAULT_MODEL = config["default_model"]
if not MODEL_NAME:
    MODEL_NAME = API_DEFAULT_MODEL

# ======================== ФУНКЦИИ =========================

def generate_post():
    # Выбираем случайную тему
    topic = random.choice(TOPICS)
    print(f"[DEBUG] Выбрана тема: {topic}")

    headers = API_HEADERS_FUNC(DEEPSEEK_API_KEY)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — автор канала «Скептик с EBITDA».\n"
                    "Стиль: дерзкий, саркастичный, с конкретными цифрами из отчётности.\n"
                    "НЕ выводи <think>, рассуждения — только готовый пост.\n"
                    "Пост должен быть не длиннее 600 символов (4–5 коротких абзацев).\n"
                    "Используй HTML: <b>жирный</b> для ключевых цифр и заголовка.\n"
                    "Каждый абзац начинай с эмодзи (📦, 💰, 📊, ⚠️, 🔥, 📉).\n"
                    "НЕ используй разделители — только пустые строки между абзацами.\n"
                    "В конце — чёткий Action Item с ✅ (одно предложение).\n"
                    "После текста поставь === и краткое описание картинки (на английском, 3–4 слова)."
                )
            },
            {
                "role": "user",
                "content": f"Напиши короткий пост на тему: {topic}. Используй реальные цифры из последних отчётов."
            }
        ],
        "temperature": 0.85,
        "max_tokens": 450
    }

    print(f"[DEBUG] Provider: {API_PROVIDER}, Model: {MODEL_NAME}")
    print(f"[DEBUG] URL: {API_URL}")

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        status = response.status_code
        response_text = response.text

        print(f"[DEBUG] Status: {status}")
        print(f"[DEBUG] Response (first 500 chars): {response_text[:500]}")

        if status != 200:
            raise Exception(f"API вернул {status}: {response_text}")

        data = response.json()
        if "choices" not in data or not data["choices"]:
            raise Exception(f"Ответ не содержит 'choices': {data}")

        full_text = data["choices"][0]["message"]["content"]
        if not full_text:
            raise Exception("Пустой ответ от API")

        if "===" in full_text:
            parts = full_text.split("===", 1)
            post_text = parts[0].strip()
            image_prompt = parts[1].strip() if len(parts) > 1 else ""
        else:
            post_text = full_text.strip()
            image_prompt = ""

        if len(image_prompt) < 10:
            image_prompt = "business finance sarcastic illustration"
            print("[WARN] Промпт для картинки был пуст, использован стандартный")

        return post_text, image_prompt

    except Exception as e:
        raise Exception(f"Ошибка при обработке ответа: {e}")

def generate_image(prompt):
    try:
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1200&height=800"
        print(f"[DEBUG] Pollinations URL: {url}")

        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            with open("temp_image.jpg", "wb") as f:
                f.write(response.content)
            return "temp_image.jpg"
        else:
            print(f"[ERROR] Pollinations status {response.status_code}")
            return None
    except Exception as e:
        print(f"[ERROR] Pollinations error: {e}")
        return None

def publish_to_telegram(text, image_path):
    try:
        # Обрезаем до 650 символов, чтобы гарантированно вписаться в лимит 1024
        if len(text) > 650:
            text = text[:650] + "… Читать далее в канале."
            print(f"[WARN] Текст обрезан до {len(text)} символов")

        print(f"[DEBUG] Длина caption: {len(text)} символов")

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": text,
                "parse_mode": "HTML"
            }
            response = requests.post(url, files=files, data=data, timeout=30)
        if response.status_code != 200:
            print(f"[ERROR] Telegram ответ: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"[ERROR] Ошибка публикации: {e}")
        return False

def job():
    print(f"[{datetime.now()}] Генерация поста...")
    try:
        post_text, image_prompt = generate_post()
        print(f"[{datetime.now()}] Текст получен, промпт: {image_prompt[:50]}...")
        image_path = generate_image(image_prompt)
        if not image_path:
            print(f"[{datetime.now()}] ОШИБКА: не удалось сгенерировать картинку")
            return
        success = publish_to_telegram(post_text, image_path)
        if success:
            print(f"[{datetime.now()}] ✅ Пост опубликован!")
        else:
            print(f"[{datetime.now()}] ❌ Ошибка публикации")
    except Exception as e:
        print(f"[{datetime.now()}] ❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()

# ======================== ВЕБ-СЕРВЕР =========================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                job()
                output = sys.stdout.getvalue()
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(f"✅ Успешно!\n\n{output}".encode())
                except BrokenPipeError:
                    pass
            except Exception as e:
                output = sys.stdout.getvalue()
                error_text = traceback.format_exc()
                self.send_response(500)
                self.end_headers()
                try:
                    self.wfile.write(f"❌ ОШИБКА: {str(e)}\n\n{output}\n\nСТЕК:\n{error_text}".encode())
                except BrokenPipeError:
                    pass
            finally:
                sys.stdout = old_stdout
        else:
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(b"OK")
            except BrokenPipeError:
                pass

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=start_health_server, daemon=True).start()

# ======================== САМОПИНГ =========================

def keep_alive():
    url = "https://skeptik-bot.onrender.com"
    while True:
        try:
            urllib.request.urlopen(url, timeout=10)
            print("[keep-alive] Пинг успешен")
        except Exception as e:
            print(f"[keep-alive] Ошибка пинга: {e}")
        time.sleep(600)

threading.Thread(target=keep_alive, daemon=True).start()

# ======================== РАСПИСАНИЕ =========================

schedule.every().day.at("10:00").do(job)

print("Бот запущен. Ожидание расписания...")
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}")
print(f"URL: {API_URL}")

while True:
    schedule.run_pending()
    time.sleep(60)