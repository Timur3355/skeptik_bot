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
import re

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

API_PROVIDER = os.getenv("API_PROVIDER", "openrouter").lower()
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek/deepseek-chat:free")

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
        "default_model": "deepseek-v3",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "deepseek/deepseek-chat:free",
        "headers": lambda key: {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
            # Убраны HTTP-Referer и X-Title, чтобы избежать проблем с кодировкой
        }
    }
}

config = PROVIDER_CONFIG.get(API_PROVIDER, PROVIDER_CONFIG["openrouter"])
API_URL = config["url"]
API_HEADERS_FUNC = config["headers"]
API_DEFAULT_MODEL = config["default_model"]
if not MODEL_NAME:
    MODEL_NAME = API_DEFAULT_MODEL

pending_posts = {}

# ======================== ОЧИСТКА ТЕКСТА =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    lines = text.split('\n')
    clean_lines = []
    start = False
    for line in lines:
        if not start and not line.strip():
            continue
        if not start and re.match(r'^[\U0001F000-\U0001FFFF]|^<b>|^\d', line.strip()):
            start = True
        if start:
            clean_lines.append(line)
    if not clean_lines:
        for i, line in enumerate(lines):
            if line.strip() and not re.search(r'\?$', line.strip()) and len(line.strip()) > 10:
                clean_lines = lines[i:]
                break
    text = '\n'.join(clean_lines)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

# ======================== ГЕНЕРАЦИЯ ПОСТА =========================
def generate_post():
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
                    "НЕ выводи внутренние рассуждения, теги <think>, пояснения — только готовый пост.\n"
                    "Пост должен быть строго до 500 символов (3–4 коротких абзаца).\n"
                    "Используй HTML: <b>жирный</b> для цифр, эмодзи в начале абзацев.\n"
                    "В конце — Action Item с ✅.\n"
                    "После текста поставь === и краткое описание картинки (на английском, 3–4 слова)."
                )
            },
            {
                "role": "user",
                "content": f"Напиши короткий пост на тему: {topic}. Используй реальные цифры из последних отчётов."
            }
        ],
        "temperature": 0.85,
        "max_tokens": 350
    }

    print(f"[DEBUG] Provider: {API_PROVIDER}, Model: {MODEL_NAME}")
    print(f"[DEBUG] URL: {API_URL}")

    for attempt in range(3):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
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

            full_text = clean_text(full_text)

            if "===" in full_text:
                parts = full_text.split("===", 1)
                post_text = parts[0].strip()
                image_prompt = parts[1].strip() if len(parts) > 1 else ""
            else:
                post_text = full_text.strip()
                image_prompt = ""

            if len(image_prompt) > 200:
                image_prompt = image_prompt[:200]
                print("[WARN] Промпт для картинки обрезан до 200 символов")

            if len(image_prompt) < 10:
                image_prompt = "business finance sarcastic illustration"
                print("[WARN] Промпт для картинки был пуст, использован стандартный")

            return post_text, image_prompt

        except requests.exceptions.Timeout:
            print(f"[WARN] Попытка {attempt+1} из 3: таймаут, повтор через 5 сек...")
            time.sleep(5)
        except Exception as e:
            print(f"[ERROR] Ошибка на попытке {attempt+1}: {e}")
            if attempt == 2:
                raise
            time.sleep(3)

    raise Exception("Не удалось получить ответ от API после 3 попыток")

# ======================== ГЕНЕРАЦИЯ КАРТИНКИ =========================
def generate_image(prompt):
    try:
        unique_suffix = f" {random.randint(1, 100000)}"
        full_prompt = prompt + unique_suffix
        encoded_prompt = urllib.parse.quote(full_prompt)
        seed = random.randint(1, 999999)
        timestamp = int(time.time())
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1200&height=800&seed={seed}&t={timestamp}"
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

# ======================== ПУБЛИКАЦИЯ =========================
def publish_to_telegram(text, image_path):
    try:
        if len(text) > 750:
            text = text[:750] + "… Читать далее в канале."
            print(f"[WARN] Текст обрезан до {len(text)} символов")

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

# ======================== ОТПРАВКА НА МОДЕРАЦИЮ =========================
def send_for_approval(post_text, image_path, image_prompt, session_id):
    caption = f"📝 Новый пост на проверку:\n\n{post_text}"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": ADMIN_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps({
                    "inline_keyboard": [
                        [
                            {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                            {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                            {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                        ]
                    ]
                })
            }
            response = requests.post(url, files=files, data=data, timeout=30)
            if response.status_code != 200:
                print(f"[ERROR] Не удалось отправить на модерацию: {response.text}")
                return False
            return True
    except Exception as e:
        print(f"[ERROR] Ошибка отправки на модерацию: {e}")
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

        session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
        pending_posts[session_id] = {
            "text": post_text,
            "image_path": image_path,
            "image_prompt": image_prompt
        }
        success = send_for_approval(post_text, image_path, image_prompt, session_id)
        if success:
            print(f"[{datetime.now()}] ✅ Пост отправлен на модерацию (ID: {session_id})")
        else:
            print(f"[{datetime.now()}] ❌ Ошибка отправки на модерацию")
            pending_posts.pop(session_id, None)
    except Exception as e:
        print(f"[{datetime.now()}] ❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()

# ======================== ОБРАБОТЧИК КНОПОК =========================
def process_callback(callback_data, chat_id, message_id):
    parts = callback_data.split('_', 1)
    if len(parts) != 2:
        return
    action, session_id = parts
    if session_id not in pending_posts:
        answer_callback(chat_id, message_id, "⏳ Этот черновик уже обработан или устарел.")
        return

    draft = pending_posts[session_id]
    if action == "approve":
        ok = publish_to_telegram(draft["text"], draft["image_path"])
        if ok:
            answer_callback(chat_id, message_id, "✅ Пост успешно опубликован!")
        else:
            answer_callback(chat_id, message_id, "❌ Ошибка публикации, проверьте логи.")
        pending_posts.pop(session_id, None)

    elif action == "regenerate":
        answer_callback(chat_id, message_id, "🔄 Генерирую новый вариант...")
        try:
            new_text, new_prompt = generate_post()
            new_image_path = generate_image(new_prompt)
            if not new_image_path:
                answer_callback(chat_id, message_id, "❌ Не удалось сгенерировать картинку.")
                return
            new_session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
            pending_posts[new_session_id] = {
                "text": new_text,
                "image_path": new_image_path,
                "image_prompt": new_prompt
            }
            send_for_approval(new_text, new_image_path, new_prompt, new_session_id)
            pending_posts.pop(session_id, None)
            answer_callback(chat_id, message_id, "🔄 Новый пост отправлен на проверку.")
        except Exception as e:
            answer_callback(chat_id, message_id, f"❌ Ошибка перегенерации: {str(e)[:100]}")

    elif action == "reject":
        pending_posts.pop(session_id, None)
        answer_callback(chat_id, message_id, "❌ Пост отклонён и удалён.")

def answer_callback(chat_id, message_id, text):
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        send_data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        requests.post(send_url, json=send_data, timeout=10)
    except Exception as e:
        print(f"[ERROR] Ошибка отправки ответа на callback: {e}")

def poll_updates():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["callback_query"]
            }
            response = requests.get(url, params=params, timeout=35)
            if response.status_code != 200:
                print(f"[ERROR] getUpdates вернул {response.status_code}")
                time.sleep(5)
                continue
            data = response.json()
            if not data.get("ok"):
                print(f"[ERROR] getUpdates ошибка: {data}")
                time.sleep(5)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    cb = update["callback_query"]
                    cb_data = cb.get("data")
                    if cb_data:
                        chat_id = cb["message"]["chat"]["id"]
                        message_id = cb["id"]
                        process_callback(cb_data, chat_id, message_id)
                        try:
                            answer_callback_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                            answer_data = {
                                "callback_query_id": cb["id"],
                                "text": "Обрабатываю..."
                            }
                            requests.post(answer_callback_url, json=answer_data, timeout=10)
                        except:
                            pass
        except Exception as e:
            print(f"[ERROR] Ошибка в poll_updates: {e}")
            time.sleep(5)

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

# ======================== ПОЛЛИНГ =========================
threading.Thread(target=poll_updates, daemon=True).start()

# ======================== РАСПИСАНИЕ =========================
schedule.every().day.at("10:00").do(job)

print("Бот запущен. Ожидание расписания...")
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}")
print(f"URL: {API_URL}")

while True:
    schedule.run_pending()
    time.sleep(60)