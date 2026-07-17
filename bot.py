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
import sqlite3
from contextlib import closing

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")          # ID канала (с минусом)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")                # Ваш личный ID

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
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://skeptik-bot.onrender.com",
            "X-Title": "Скептик с EBITDA"
        }
    }
}

config = PROVIDER_CONFIG.get(API_PROVIDER, PROVIDER_CONFIG["openrouter"])
API_URL = config["url"]
API_HEADERS_FUNC = config["headers"]
API_DEFAULT_MODEL = config["default_model"]
if not MODEL_NAME:
    MODEL_NAME = API_DEFAULT_MODEL

# ======================== БАЗА ДАННЫХ =========================
DB_PATH = "posts.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE,
                text TEXT,
                image_path TEXT,
                image_prompt TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
        conn.commit()

init_db()

def save_post(session_id, text, image_path, image_prompt):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            'INSERT OR REPLACE INTO posts (session_id, text, image_path, image_prompt, status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, text, image_path, image_prompt, 'pending', datetime.now().isoformat())
        )
        conn.commit()
    print(f"[DEBUG] Пост сохранён в БД: session_id={session_id}")

def get_post(session_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.execute('SELECT text, image_path, image_prompt, status FROM posts WHERE session_id = ?', (session_id,))
        row = cursor.fetchone()
        if row:
            print(f"[DEBUG] Найден пост: session_id={session_id}, status={row[3]}")
            return {'text': row[0], 'image_path': row[1], 'image_prompt': row[2], 'status': row[3]}
        else:
            print(f"[DEBUG] Пост не найден: session_id={session_id}")
            return None

def update_post_status(session_id, status):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('UPDATE posts SET status = ? WHERE session_id = ?', (status, session_id))
        if status == 'published':
            conn.execute('UPDATE posts SET published_at = ? WHERE session_id = ?', (datetime.now().isoformat(), session_id))
        conn.commit()
    print(f"[DEBUG] Статус обновлён: session_id={session_id}, status={status}")

def delete_post(session_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('DELETE FROM posts WHERE session_id = ?', (session_id,))
        conn.commit()
    print(f"[DEBUG] Пост удалён: session_id={session_id}")

# ======================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

# ======================== ГЕНЕРАЦИЯ ПОСТА (БЕЗ HTML) =========================
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
                    "Стиль: дерзкий, саркастичный, с конкретными цифрами.\n"
                    "НЕ выводи <think>, рассуждения — только готовый пост.\n"
                    "Пост должен быть длиной 400–500 символов (4–5 абзацев).\n"
                    "Используй эмодзи в начале абзацев (например, 📦, 💰, 📊, ⚠️, 🔥, 📉).\n"
                    "НЕ используй HTML-теги (<b>, <i> и т.д.).\n"
                    "В конце — Action Item с ✅ (одно предложение).\n"
                    "После текста === и описание картинки (англ., 3–4 слова)."
                )
            },
            {
                "role": "user",
                "content": f"Напиши пост на тему: {topic}. Используй реальные цифры из последних отчётов."
            }
        ],
        "temperature": 0.85,
        "max_tokens": 280
    }

    for attempt in range(3):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            if response.status_code != 200:
                raise Exception(f"API вернул {response.status_code}: {response.text}")
            data = response.json()
            if "choices" not in data or not data["choices"]:
                raise Exception("Ответ не содержит choices")
            full_text = data["choices"][0]["message"]["content"]
            if not full_text:
                raise Exception("Пустой ответ")
            full_text = clean_text(full_text)
            if "===" in full_text:
                parts = full_text.split("===", 1)
                post_text = parts[0].strip()
                image_prompt = parts[1].strip() if len(parts) > 1 else ""
            else:
                post_text = full_text.strip()
                image_prompt = ""
            if len(image_prompt) < 10:
                image_prompt = "business finance sarcastic illustration"
            return post_text, image_prompt
        except requests.exceptions.Timeout:
            print(f"[WARN] Попытка {attempt+1} таймаут, повтор...")
            time.sleep(5)
        except Exception as e:
            print(f"[ERROR] Попытка {attempt+1}: {e}")
            if attempt == 2:
                raise
            time.sleep(3)
    raise Exception("Не удалось получить ответ от API")

# ======================== ГЕНЕРАЦИЯ КАРТИНКИ =========================
def generate_image(prompt):
    try:
        unique_suffix = f" {random.randint(1, 100000)}"
        full_prompt = prompt + unique_suffix
        encoded = urllib.parse.quote(full_prompt)
        seed = random.randint(1, 999999)
        timestamp = int(time.time())
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=800&seed={seed}&t={timestamp}"
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

# ======================== ПУБЛИКАЦИЯ В КАНАЛ (БЕЗ HTML) =========================
def publish_to_telegram(text, image_path):
    try:
        if not os.path.exists(image_path):
            print(f"[ERROR] Файл {image_path} не найден")
            return False

        # Обрезаем до 950 символов (запас)
        if len(text) > 950:
            text = text[:950]
            last_space = text.rfind(' ')
            if last_space > 0:
                text = text[:last_space] + "… Читать далее в канале."
            else:
                text = text + "… Читать далее в канале."
        print(f"[DEBUG] Длина текста: {len(text)} символов")

        # Проверяем права бота
        check_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember"
        check_params = {"chat_id": TELEGRAM_CHAT_ID, "user_id": "me"}
        check_response = requests.get(check_url, params=check_params, timeout=10)
        if check_response.status_code == 200:
            check_data = check_response.json()
            if check_data.get("ok") and check_data.get("result", {}).get("status") not in ["administrator", "creator"]:
                print("[ERROR] Бот не администратор канала!")
                return False
        else:
            print(f"[ERROR] Не удалось проверить права: {check_response.text}")

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": text
                # parse_mode отсутствует
            }
            response = requests.post(url, files=files, data=data, timeout=30)
        if response.status_code == 200:
            print("[DEBUG] Публикация успешна")
            return True
        else:
            print(f"[ERROR] Telegram ответ: {response.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Ошибка публикации: {e}")
        traceback.print_exc()
        return False

# ======================== ОТПРАВКА НА ПРОВЕРКУ =========================
def send_for_approval(post_text, image_path, image_prompt, session_id):
    save_post(session_id, post_text, image_path, image_prompt)
    # Обрезаем для отображения в личке
    if len(post_text) > 950:
        post_text = post_text[:950]
        last_space = post_text.rfind(' ')
        if last_space > 0:
            post_text = post_text[:last_space] + "… Читать далее в канале."
        else:
            post_text = post_text + "… Читать далее в канале."
    caption = f"📝 Новый пост на проверку:\n\n{post_text}"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": ADMIN_CHAT_ID,
                "caption": caption,
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

# ======================== ОБРАБОТЧИК КНОПОК =========================
def process_callback(callback_data, chat_id, message_id):
    parts = callback_data.split('_', 1)
    if len(parts) != 2:
        return
    action, session_id = parts
    print(f"[DEBUG] Callback: action={action}, session_id={session_id}")
    post_data = get_post(session_id)
    if not post_data:
        answer_callback(chat_id, message_id, "⏳ Черновик не найден (возможно, устарел).")
        return
    if post_data["status"] == "published":
        answer_callback(chat_id, message_id, "ℹ️ Этот пост уже был опубликован ранее.")
        return
    if post_data["status"] == "rejected":
        answer_callback(chat_id, message_id, "ℹ️ Этот пост был отклонён.")
        return

    if action == "approve":
        for attempt in range(3):
            ok = publish_to_telegram(post_data["text"], post_data["image_path"])
            if ok:
                update_post_status(session_id, 'published')
                answer_callback(chat_id, message_id, "✅ Пост опубликован!")
                return
            else:
                print(f"[WARN] Попытка публикации {attempt+1} не удалась")
                time.sleep(2)
        answer_callback(chat_id, message_id, "❌ Не удалось опубликовать пост. Проверьте логи.")
    elif action == "regenerate":
        answer_callback(chat_id, message_id, "🔄 Генерирую новый...")
        try:
            new_text, new_prompt = generate_post()
            new_image_path = generate_image(new_prompt)
            if not new_image_path:
                answer_callback(chat_id, message_id, "❌ Ошибка генерации картинки")
                return
            new_session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
            delete_post(session_id)  # удаляем старый
            send_for_approval(new_text, new_image_path, new_prompt, new_session_id)
            answer_callback(chat_id, message_id, "🔄 Новый пост отправлен на проверку.")
        except Exception as e:
            answer_callback(chat_id, message_id, f"❌ Ошибка перегенерации: {str(e)[:100]}")
    elif action == "reject":
        update_post_status(session_id, 'rejected')
        answer_callback(chat_id, message_id, "❌ Пост отклонён.")

def answer_callback(chat_id, message_id, text):
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        send_data = {"chat_id": chat_id, "text": text}
        requests.post(send_url, json=send_data, timeout=10)
    except Exception as e:
        print(f"[ERROR] Ошибка ответа на callback: {e}")

# ======================== ПОЛЛИНГ ОБНОВЛЕНИЙ =========================
def poll_updates():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 30, "allowed_updates": ["callback_query"]}
            response = requests.get(url, params=params, timeout=35)
            if response.status_code != 200:
                print(f"[ERROR] getUpdates ошибка {response.status_code}")
                time.sleep(5)
                continue
            data = response.json()
            if not data.get("ok"):
                print(f"[ERROR] getUpdates: {data}")
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
                            answer_data = {"callback_query_id": cb["id"], "text": "Обрабатываю..."}
                            requests.post(answer_callback_url, json=answer_data, timeout=10)
                        except:
                            pass
        except Exception as e:
            print(f"[ERROR] poll_updates: {e}")
            time.sleep(5)

# ======================== ОСНОВНАЯ ЗАДАЧА (9:55) =========================
def job():
    print(f"[{datetime.now()}] Генерация поста для проверки...")
    try:
        post_text, image_prompt = generate_post()
        image_path = generate_image(image_prompt)
        if not image_path:
            print("[ERROR] Не удалось сгенерировать картинку")
            return
        session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
        ok = send_for_approval(post_text, image_path, image_prompt, session_id)
        if ok:
            print(f"[{datetime.now()}] ✅ Пост отправлен на модерацию (ID: {session_id})")
        else:
            print(f"[{datetime.now()}] ❌ Ошибка отправки на модерацию")
    except Exception as e:
        print(f"[ERROR] job: {e}")
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

# ======================== ЗАПУСК ПОЛЛИНГА =========================
threading.Thread(target=poll_updates, daemon=True).start()

# ======================== РАСПИСАНИЕ =========================
schedule.every().day.at("09:55").do(job)

print("Бот запущен. Ожидание расписания...")
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}")
print(f"URL: {API_URL}")

while True:
    schedule.run_pending()
    time.sleep(60)