import os
import re
import json
import time
import random
import sqlite3
import requests
import threading
import schedule
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import quote
from PIL import Image

# ============================================================
# 1. КОНФИГУРАЦИЯ (из переменных окружения)
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_CHAT_ID")
CHANNEL_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("DEEPSEEK_API_KEY") or "sk-or-v1-fake-key-for-testing"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat:free"

print(f"[START] BOT_TOKEN: {BOT_TOKEN[:5]}...")
print(f"[START] ADMIN_ID: {ADMIN_ID}")
print(f"[START] CHANNEL_ID: {CHANNEL_ID}")

# ============================================================
# 2. БАЗА ДАННЫХ (SQLite)
# ============================================================
DB_PATH = "posts.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE,
            text TEXT,
            image_path TEXT,
            image_prompt TEXT,
            topic TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scheduled_time TIMESTAMP,
            message_id INTEGER,
            rating INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if fetch:
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()
        return rows
    c.execute(query, params)
    conn.commit()
    conn.close()

# ============================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def clean_text(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

def split_text(text, max_len=3000):
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        chunk = text[:max_len]
        last_space = chunk.rfind(' ')
        if last_space > 0:
            chunk = chunk[:last_space]
        parts.append(chunk)
        text = text[len(chunk):].strip()
    return parts

def send_telegram(chat_id, text, parse_mode=None, buttons=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        r = requests.post(url, json=payload, timeout=30)
        return r.json()
    except Exception as e:
        print(f"[ERROR] send_telegram: {e}")
        return None

def send_photo(chat_id, photo_path, caption="", buttons=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as f:
        files = {"photo": f}
        data = {"chat_id": chat_id, "caption": caption}
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        try:
            r = requests.post(url, files=files, data=data, timeout=30)
            return r.json()
        except Exception as e:
            print(f"[ERROR] send_photo: {e}")
            return None

def generate_image(prompt):
    # Упрощённая генерация картинки через Pollinations.ai с уникальным seed
    prompt = prompt[:100]
    unique = f" {random.randint(1, 100000)}"
    full_prompt = prompt + unique
    encoded = quote(full_prompt)
    seed = random.randint(1, 999999)
    ts = int(time.time())
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=800&seed={seed}&t={ts}"
    print(f"[DEBUG] Pollinations URL: {url}")
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open("temp_image.jpg", "wb") as f:
                f.write(resp.content)
            return "temp_image.jpg"
        else:
            print(f"[ERROR] Pollinations вернул {resp.status_code}")
            return None
    except Exception as e:
        print(f"[ERROR] generate_image: {e}")
        return None

# ============================================================
# 4. ГЕНЕРАЦИЯ ПОСТА (С ИСПОЛЬЗОВАНИЕМ API)
# ============================================================
def generate_post():
    topics = [
        "логистические провалы Ozon: затраты, сроки доставки, убытки",
        "штрафы и возвраты Wildberries: как компания зарабатывает на продавцах",
        "долговая нагрузка Магнита: кредиты, проценты, соотношение долга к EBITDA",
        "маркетинговые расходы Ozon: сколько тратят на привлечение клиентов и окупается ли это",
        "технологические проблемы Wildberries: баги, сбои, инвестиции в IT",
        "стратегия экспансии Магнита: открытие и закрытие магазинов, эффективность",
        "сравнительный анализ трёх ритейлеров: кто хуже?"
    ]
    topic = random.choice(topics)
    print(f"[DEBUG] Выбрана тема: {topic}")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "HTTP-Referer": "https://skeptik-bot.onrender.com",
        "X-Title": "Скептик с EBITDA"
    }
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — автор канала «Скептик с EBITDA».\n"
                    "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
                    "НЕ выводи <think>, рассуждения — только пост.\n"
                    "Пост должен быть коротким: 3–4 абзаца, примерно 400–500 символов.\n"
                    "Используй эмодзи в начале абзацев, НЕ используй HTML.\n"
                    "В конце — Action Item с ✅.\n"
                    "Указывай период и источник (например, Q3 2023).\n"
                    "После Action Item добавь ссылку на источник.\n"
                    "В конце поста добавь 3–5 хештегов, начинающихся с #.\n"
                    "После текста === и описание картинки (англ., 3–4 слова)."
                )
            },
            {
                "role": "user",
                "content": f"Напиши пост на тему: {topic}. Используй реальные цифры из отчётов."
            }
        ],
        "temperature": 0.85,
        "max_tokens": 200
    }

    for attempt in range(3):
        try:
            print(f"[DEBUG] Отправка запроса в API (попытка {attempt+1})...")
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            print(f"[DEBUG] Статус ответа: {response.status_code}")
            if response.status_code != 200:
                raise Exception(f"API вернул {response.status_code}: {response.text}")

            data = response.json()
            if "choices" not in data:
                raise Exception("Нет choices в ответе")

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
                image_prompt = "retail comparison illustration, business graph, sarcastic, modern, colorful"

            return post_text, image_prompt, topic

        except requests.exceptions.Timeout:
            print(f"[WARN] Попытка {attempt+1} таймаут")
            time.sleep(5)
        except Exception as e:
            print(f"[ERROR] Попытка {attempt+1}: {e}")
            if attempt == 2:
                raise
            time.sleep(3)

    raise Exception("Не удалось получить ответ от API")

# ============================================================
# 5. ПУБЛИКАЦИЯ В КАНАЛ И МОДЕРАЦИЯ
# ============================================================
def publish_to_channel(text, image_path):
    """Публикует пост в канал с фото и полным текстом (разбивает при необходимости)"""
    if not os.path.exists(image_path):
        print("[ERROR] Картинка не найдена, публикуем только текст")
        send_telegram(CHANNEL_ID, text)
        return True

    # Отправляем фото с кратким началом (до 1000 символов)
    first_part = text[:1000]
    # Ищем последний пробел, чтобы не обрезать слово
    last_space = first_part.rfind(' ')
    if last_space > 0:
        first_part = first_part[:last_space] + "..."

    resp = send_photo(CHANNEL_ID, image_path, caption=first_part)
    if not resp or not resp.get("ok"):
        print("[ERROR] Не удалось отправить фото, пробуем текст")
        send_telegram(CHANNEL_ID, text)
        return True

    # Если текст длиннее 1000 символов, отправляем продолжение текстом
    if len(text) > 1000:
        continuation = text[len(first_part)-3:]  # убираем "..."
        # разбиваем продолжение на части по 4000 символов
        parts = split_text(continuation, max_len=4000)
        for i, part in enumerate(parts, 1):
            send_telegram(CHANNEL_ID, f"📎 Продолжение (часть {i}/{len(parts)}):\n\n{part}")

    return True

def send_for_approval(post_text, image_path, image_prompt, topic):
    """Отправляет пост на модерацию админу"""
    session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
    db_execute(
        "INSERT OR REPLACE INTO posts (session_id, text, image_path, image_prompt, topic, status) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, post_text, image_path, image_prompt, topic, 'pending')
    )

    # Отправляем фото с первой частью
    first_part = post_text[:1000]
    last_space = first_part.rfind(' ')
    if last_space > 0:
        first_part = first_part[:last_space] + "..."

    buttons = [
        [
            {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
            {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
            {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
            {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
        ]
    ]

    resp = send_photo(ADMIN_ID, image_path, caption=f"📝 Новый пост на проверку:\n\n{first_part}", buttons=buttons)
    if not resp or not resp.get("ok"):
        print("[ERROR] Не удалось отправить фото на модерацию, пробуем текстом")
        send_telegram(ADMIN_ID, f"📝 Новый пост на проверку (без фото):\n\n{post_text}", buttons=buttons)

    # Отправляем полный текст отдельным сообщением (разбиваем, если длинный)
    full_parts = split_text(post_text, max_len=4000)
    for i, part in enumerate(full_parts, 1):
        send_telegram(ADMIN_ID, f"📄 Полный текст поста (часть {i}/{len(full_parts)}):\n\n{part}")

    return True

# ============================================================
# 6. ОБРАБОТЧИК КНОПОК (callback)
# ============================================================
def handle_callback(data):
    action, session_id = data.split('_', 1)
    print(f"[DEBUG] Callback: {action}, {session_id}")

    rows = db_execute(
        "SELECT text, image_path, topic, status FROM posts WHERE session_id = ?",
        (session_id,), fetch=True
    )
    if not rows:
        send_telegram(ADMIN_ID, "⏳ Черновик устарел или уже обработан.")
        return

    row = rows[0]
    post_text, image_path, topic, status = row

    if status != 'pending':
        send_telegram(ADMIN_ID, f"ℹ️ Пост уже {status}.")
        return

    if action == "approve":
        # Планируем публикацию на завтра 10:00 МСК
        now = datetime.now()
        scheduled_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= scheduled_time:
            scheduled_time += timedelta(days=1)
        db_execute(
            "UPDATE posts SET status = 'approved', scheduled_time = ? WHERE session_id = ?",
            (scheduled_time.isoformat(), session_id)
        )
        send_telegram(ADMIN_ID, f"✅ Пост одобрен, запланирован на {scheduled_time.strftime('%d.%m.%Y %H:%M')} МСК.")

    elif action == "regenerate":
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_image = generate_image(new_prompt)
            if not new_image:
                new_image = None
            # Удаляем старый пост и создаём новый
            db_execute("DELETE FROM posts WHERE session_id = ?", (session_id,))
            send_for_approval(new_text, new_image, new_prompt, new_topic)
            send_telegram(ADMIN_ID, "🔄 Новый пост отправлен на проверку.")
        except Exception as e:
            send_telegram(ADMIN_ID, f"❌ Ошибка перегенерации: {str(e)[:100]}")

    elif action == "edit":
        send_telegram(ADMIN_ID, "✏️ Пришли новый текст поста (без картинки).")
        # Ждём следующее сообщение от админа
        # (упрощённо: сохраняем session_id в память, но для простоты реализуем позже)

    elif action == "reject":
        db_execute("UPDATE posts SET status = 'rejected' WHERE session_id = ?", (session_id,))
        send_telegram(ADMIN_ID, "❌ Пост отклонён.")

# ============================================================
# 7. ОСНОВНАЯ ЗАДАЧА (ГЕНЕРАЦИЯ ПОСТА)
# ============================================================
def job(auto_publish=False):
    print(f"[DEBUG] job started at {datetime.now()}")
    try:
        post_text, image_prompt, topic = generate_post()
        image_path = generate_image(image_prompt)
        if auto_publish:
            # Сразу публикуем в канал
            success = publish_to_channel(post_text, image_path)
            if success:
                print(f"[DEBUG] Авто-пост опубликован в {datetime.now()}")
                send_telegram(ADMIN_ID, f"✅ Авто-пост опубликован в {datetime.now().strftime('%H:%M')}")
            else:
                print("[ERROR] Ошибка авто-публикации")
                send_telegram(ADMIN_ID, "❌ Ошибка авто-публикации")
        else:
            # Отправляем на модерацию
            send_for_approval(post_text, image_path, image_prompt, topic)
            print(f"[DEBUG] Пост отправлен на модерацию в {datetime.now()}")
    except Exception as e:
        print(f"[ERROR] job: {e}")
        send_telegram(ADMIN_ID, f"❌ Ошибка: {str(e)[:200]}")

# ============================================================
# 8. ПУБЛИКАЦИЯ ЗАПЛАНИРОВАННЫХ ПОСТОВ
# ============================================================
def publish_scheduled():
    print(f"[DEBUG] Проверка запланированных постов в {datetime.now()}")
    now = datetime.now().isoformat()
    rows = db_execute(
        "SELECT session_id, text, image_path FROM posts WHERE status = 'approved' AND scheduled_time <= ?",
        (now,), fetch=True
    )
    for row in rows:
        session_id, post_text, image_path = row
        if publish_to_channel(post_text, image_path):
            db_execute("UPDATE posts SET status = 'published' WHERE session_id = ?", (session_id,))
            print(f"[DEBUG] Опубликован пост {session_id}")
        else:
            print(f"[ERROR] Не удалось опубликовать пост {session_id}")

# ============================================================
# 9. ВЕБ-СЕРВЕР ДЛЯ RENDER
# ============================================================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Starting test... Check logs and Telegram.")
            # Запускаем генерацию в синхронном режиме (не в фоне)
            threading.Thread(target=job, args=(False,)).start()
            return
        elif self.path == '/test_publish':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Starting auto-publish... Check logs and Telegram.")
            threading.Thread(target=job, args=(True,)).start()
            return
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"[SERVER] Запущен на порту {port}")
    server.serve_forever()

# ============================================================
# 10. ЗАПУСК ПОТОКОВ И РАСПИСАНИЕ
# ============================================================
# Запускаем веб-сервер в отдельном потоке
threading.Thread(target=run_server, daemon=True).start()

# Расписание
schedule.every().day.at("15:00").do(lambda: job(auto_publish=False))  # 18:00 МСК
schedule.every().day.at("07:00").do(publish_scheduled)                # 10:00 МСК

print("[BOT] Запущен. Ожидание расписания...")

while True:
    try:
        schedule.run_pending()
        time.sleep(60)
    except Exception as e:
        print(f"[ERROR] В основном цикле: {e}")
        time.sleep(60)