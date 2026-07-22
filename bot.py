import requests
import time
import schedule
from datetime import datetime, timedelta
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
import pytz
import feedparser
from PIL import Image

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

API_PROVIDER = os.getenv("API_PROVIDER", "openrouter").lower()
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek/deepseek-chat:free")

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

DAY_TOPICS = {
    0: "логистические провалы Ozon: затраты, сроки доставки, убытки",
    1: "штрафы и возвраты Wildberries: как компания зарабатывает на продавцах",
    2: "долговая нагрузка Магнита: кредиты, проценты, соотношение долга к EBITDA",
    3: "маркетинговые расходы Ozon: сколько тратят на привлечение клиентов и окупается ли это",
    4: "технологические проблемы Wildberries: баги, сбои, инвестиции в IT",
    5: "стратегия экспансии Магнита: открытие и закрытие магазинов, эффективность",
    6: "сравнительный анализ трёх ритейлеров: кто хуже?"
}

PROVIDER_CONFIG = {
    "openai": {
        "url": "https://api.chatanywhere.tech/v1/chat/completions",
        "default_model": "deepseek-v3",
        "headers": lambda key: {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
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

# ======================== RSS И АНАЛИТИКА =========================
def get_topic_from_news():
    rss_urls = [
        "https://www.rbc.ru/rss/",
        "https://www.kommersant.ru/RSS/news.xml",
        "https://lenta.ru/rss/news"
    ]
    keywords = ["ozon", "wildberries", "магнит", "ритейл", "торговля", "сеть"]
    try:
        for url in rss_urls:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.title.lower()
                if any(kw in title for kw in keywords):
                    summary = entry.summary if hasattr(entry, 'summary') else ""
                    return f"{entry.title}. {summary[:100]}"
        return DAY_TOPICS.get(datetime.now().weekday(), DAY_TOPICS[0])
    except Exception as e:
        print(f"[WARN] Ошибка RSS: {e}")
        return DAY_TOPICS.get(datetime.now().weekday(), DAY_TOPICS[0])

def get_topic_by_analytics():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT topic, rating, views, reactions FROM posts WHERE status = \'published\' AND published_at >= ? AND topic IS NOT NULL AND topic != ""',
        (week_ago,), fetch=True
    )
    if not rows:
        print("[DEBUG] Нет данных для аналитики, используем RSS")
        return get_topic_from_news()

    topic_stats = {}
    for row in rows:
        topic = row['topic']
        rating = row['rating'] or 0
        views = row['views'] or 0
        reactions = row['reactions'] or 0
        score = rating + views * 0.1 + reactions * 0.5
        if topic not in topic_stats:
            topic_stats[topic] = 0
        topic_stats[topic] += score

    if not topic_stats:
        return get_topic_from_news()

    best_topic = max(topic_stats, key=topic_stats.get)
    print(f"[DEBUG] Лучшая тема по аналитике: {best_topic} (score: {topic_stats[best_topic]:.1f})")
    return best_topic

# ======================== БАЗА ДАННЫХ =========================
if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    def get_db_connection():
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    db_type = 'postgres'
else:
    import sqlite3
    from contextlib import closing
    DB_PATH = "posts.db"
    db_type = 'sqlite'

def init_db():
    if db_type == 'postgres':
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                session_id TEXT UNIQUE,
                text TEXT,
                image_path TEXT,
                image_prompt TEXT,
                topic TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                scheduled_publish_time TIMESTAMP,
                published_at TIMESTAMP,
                edit_pending BOOLEAN DEFAULT FALSE,
                rating INTEGER DEFAULT 0,
                reposted BOOLEAN DEFAULT FALSE,
                message_id BIGINT,
                views INTEGER DEFAULT 0,
                reactions INTEGER DEFAULT 0
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_publish ON posts(scheduled_publish_time)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posts(topic)')
        conn.commit()
        cur.close()
        conn.close()
    else:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE,
                    text TEXT,
                    image_path TEXT,
                    image_prompt TEXT,
                    topic TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved_at TIMESTAMP,
                    scheduled_publish_time TIMESTAMP,
                    published_at TIMESTAMP,
                    edit_pending INTEGER DEFAULT 0,
                    rating INTEGER DEFAULT 0,
                    reposted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    views INTEGER DEFAULT 0,
                    reactions INTEGER DEFAULT 0
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_publish ON posts(scheduled_publish_time)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posts(topic)')
            conn.commit()
init_db()

def execute_query(query, params=None, fetch=False, fetchone=False):
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor if fetch or fetchone else None)
        cur.execute(query, params)
        if fetch:
            result = cur.fetchall()
        elif fetchone:
            result = cur.fetchone()
        else:
            result = None
        conn.commit()
        cur.close()
        conn.close()
        return result
    else:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(query, params)
            if fetch:
                result = [dict(row) for row in cur.fetchall()]
            elif fetchone:
                row = cur.fetchone()
                result = dict(row) if row else None
            else:
                result = None
            conn.commit()
            return result

def save_post(session_id, text, image_path, image_prompt, topic):
    if db_type == 'postgres':
        query = '''
            INSERT INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                text = EXCLUDED.text,
                image_path = EXCLUDED.image_path,
                image_prompt = EXCLUDED.image_prompt,
                topic = EXCLUDED.topic,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at
        '''
        params = (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat())
    else:
        query = 'INSERT OR REPLACE INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)'
        params = (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat())
    execute_query(query, params)
    print(f"[DEBUG] Пост сохранён: {session_id}")

def get_post(session_id):
    row = execute_query('SELECT text, image_path, image_prompt, status, scheduled_publish_time, edit_pending, rating, reposted, message_id, topic FROM posts WHERE session_id = ?', (session_id,), fetchone=True)
    return row

def update_post_text(session_id, new_text):
    execute_query('UPDATE posts SET text = ? WHERE session_id = ?', (new_text, session_id))

def update_post_status(session_id, status, scheduled_time=None):
    if scheduled_time:
        execute_query('UPDATE posts SET status = ?, scheduled_publish_time = ?, approved_at = ? WHERE session_id = ?',
                      (status, scheduled_time.isoformat(), datetime.now().isoformat(), session_id))
    else:
        execute_query('UPDATE posts SET status = ? WHERE session_id = ?', (status, session_id))

def delete_post(session_id):
    execute_query('DELETE FROM posts WHERE session_id = ?', (session_id,))

def get_approved_posts_to_publish():
    now = datetime.now().isoformat()
    rows = execute_query(
        'SELECT session_id, text, image_path FROM posts WHERE status = \'approved\' AND scheduled_publish_time <= ?',
        (now,), fetch=True
    )
    return rows

def get_weekly_stats():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT COUNT(*) as total, SUM(CASE WHEN status=\'published\' THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status=\'rejected\' THEN 1 ELSE 0 END) as rejected FROM posts WHERE created_at >= ?',
        (week_ago,), fetchone=True
    )
    return rows

# ======================== ВСПОМОГАТЕЛЬНЫЕ =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

# =========== УПРОЩЁННАЯ ФУНКЦИЯ РАЗБИВКИ ===========
def split_text(text, max_bytes=4000):
    """
    Разбивает текст на части, каждая из которых не превышает max_bytes в UTF-8.
    Режет строго по байтам, не разрывая многобайтовые символы.
    """
    if len(text.encode('utf-8')) <= max_bytes:
        return [text]

    parts = []
    while text:
        # Берём префикс размером max_bytes
        encoded = text.encode('utf-8')[:max_bytes]
        # Корректируем, чтобы не разорвать символ
        while encoded and (encoded[-1] & 0xC0) == 0x80:
            encoded = encoded[:-1]
        part = encoded.decode('utf-8', errors='ignore')
        parts.append(part)
        text = text[len(part):]
    return parts

# ======================== ГЕНЕРАЦИЯ ПОСТА =========================
def generate_post():
    topic = get_topic_by_analytics()
    print(f"[DEBUG] Выбрана тема: {topic}")

    headers = API_HEADERS_FUNC(DEEPSEEK_API_KEY)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — автор канала «Скептик с EBITDA».\n"
                    "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
                    "НЕ выводи <think>, рассуждения — только пост.\n"
                    "Пост должен быть содержательным, 4–5 абзацев, примерно 600–800 символов.\n"
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
        "max_tokens": 250
    }

    for attempt in range(3):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            if response.status_code != 200:
                raise Exception(f"API вернул {response.status_code}: {response.text}")
            data = response.json()
            if "choices" not in data:
                raise Exception("Нет choices")
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
    raise Exception("Не удалось получить ответ")

# ======================== ГЕНЕРАЦИЯ КАРТИНКИ =========================
def is_image_black(image_path):
    try:
        img = Image.open(image_path)
        if img.width < 50 or img.height < 50:
            return True
        img = img.convert('L')
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        return avg < 30
    except:
        return False

def generate_image(prompt, max_attempts=3):
    if len(prompt) > 100:
        prompt = prompt[:100]

    for attempt in range(max_attempts):
        try:
            unique = f" {random.randint(1, 100000)}"
            full_prompt = prompt + unique
            encoded = urllib.parse.quote(full_prompt)
            seed = random.randint(1, 999999)
            ts = int(time.time())
            url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=800&seed={seed}&t={ts}"
            print(f"[DEBUG] Pollinations URL (попытка {attempt+1}): {url}")

            resp = requests.get(url, timeout=90)
            if resp.status_code == 200:
                content = resp.content
                if len(content) < 1000:
                    print(f"[WARN] Слишком маленький файл ({len(content)} байт), повтор")
                    time.sleep(2)
                    continue
                with open("temp_image.jpg", "wb") as f:
                    f.write(content)
                if is_image_black("temp_image.jpg"):
                    print("[WARN] Обнаружено чёрное изображение, пробуем с упрощённым промптом")
                    os.remove("temp_image.jpg")
                    prompt = "retail illustration, business, comparison, sarcastic, modern"
                    continue
                return "temp_image.jpg"
            else:
                print(f"[WARN] Pollinations вернул {resp.status_code}, попытка {attempt+1}")
        except Exception as e:
            print(f"[WARN] Ошибка Pollinations (попытка {attempt+1}): {e}")
        time.sleep(3)

    try:
        print("[DEBUG] Последняя попытка с минимальным промптом")
        url = f"https://image.pollinations.ai/prompt/business%20illustration?width=1200&height=800&seed={random.randint(1,999999)}&t={int(time.time())}"
        resp = requests.get(url, timeout=90)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open("temp_image.jpg", "wb") as f:
                f.write(resp.content)
            if not is_image_black("temp_image.jpg"):
                return "temp_image.jpg"
    except:
        pass

    print("[ERROR] Все попытки генерации картинки провалились")
    return None

# ======================== ПУБЛИКАЦИЯ =========================
def publish_text_only(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        resp = requests.post(url, json=data, timeout=30)
        return resp.status_code == 200
    except:
        return False

def send_for_approval_no_image(post_text, topic):
    session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
    save_post(session_id, post_text, "", "", topic)
    full_parts = split_text(post_text, max_bytes=4000)
    for i, part in enumerate(full_parts, 1):
        text_data = {
            "chat_id": ADMIN_CHAT_ID,
            "text": f"📝 Новый пост на проверку (без картинки, часть {i}/{len(full_parts)}):\n\n{part}",
            "reply_markup": json.dumps({
                "inline_keyboard": [
                    [
                        {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                        {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                        {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                        {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                    ]
                ]
            })
        }
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
    return True

def publish_to_telegram(text, image_path, session_id=None):
    if not os.path.exists(image_path):
        return False

    # Для подписи к фото – не более 1000 байт
    parts = split_text(text, max_bytes=1000)
    first_part = parts[0] if parts else ""
    second_part = parts[1] if len(parts) > 1 else ""

    # Отправка фото
    with open(image_path, "rb") as photo:
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": first_part}
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", files={"photo": photo}, data=data, timeout=30)
        if resp.status_code != 200:
            return False
        if session_id:
            msg_data = resp.json()
            message_id = msg_data.get('result', {}).get('message_id')
            if message_id:
                execute_query('UPDATE posts SET message_id = ? WHERE session_id = ?', (message_id, session_id))

    # Если есть продолжение – разбиваем его на части по 3000 байт
    if second_part:
        continuation_parts = split_text(second_part, max_bytes=3000)
        for i, cont_part in enumerate(continuation_parts, 1):
            text_data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"📎 Продолжение (часть {i}/{len(continuation_parts)}):\n\n{cont_part}"
            }
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
    return True

def send_for_approval(post_text, image_path, image_prompt, session_id, topic):
    save_post(session_id, post_text, image_path, image_prompt, topic)

    # Подпись к фото – до 1000 байт
    first_part = split_text(post_text, max_bytes=1000)[0]
    caption = f"📝 Новый пост на проверку (начало):\n\n{first_part}..."
    with open(image_path, "rb") as photo:
        data = {
            "chat_id": ADMIN_CHAT_ID,
            "caption": caption,
            "reply_markup": json.dumps({
                "inline_keyboard": [
                    [
                        {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                        {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                        {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                        {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                    ]
                ]
            })
        }
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", files={"photo": photo}, data=data, timeout=30)

    # Полный текст разбиваем на части по 4000 байт
    full_parts = split_text(post_text, max_bytes=4000)
    for i, part in enumerate(full_parts, 1):
        text_data = {
            "chat_id": ADMIN_CHAT_ID,
            "text": f"📄 Полный текст поста (часть {i}/{len(full_parts)}):\n\n{part}"
        }
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
    return True

def schedule_publish(session_id):
    now = datetime.now(MOSCOW_TZ)
    publish_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= publish_time:
        publish_time += timedelta(days=1)
    update_post_status(session_id, 'approved', scheduled_time=publish_time)
    send_message(ADMIN_CHAT_ID, f"✅ Пост одобрен и запланирован на {publish_time.strftime('%d.%m.%Y %H:%M')} МСК.")

def send_message(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

# ======================== АВТОПОВТОР И ДАЙДЖЕСТ =========================
def check_and_repost():
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    rows = execute_query(
        'SELECT session_id, text FROM posts WHERE status = \'published\' AND reposted = 0 AND rating >= 3 AND published_at <= ?',
        (cutoff,), fetch=True
    )
    for row in rows:
        if publish_text_only(row['text']):
            execute_query('UPDATE posts SET reposted = 1 WHERE session_id = ?', (row['session_id'],))
            print(f"[DEBUG] Повторно опубликован пост {row['session_id']}")

def digest_job():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT text, rating, message_id, views, reactions FROM posts WHERE status = \'published\' AND published_at >= ? ORDER BY rating DESC LIMIT 5',
        (week_ago,), fetch=True
    )
    if not rows:
        send_message(ADMIN_CHAT_ID, "📊 За неделю нет опубликованных постов.")
        return

    digest = "📅 **Лучшие посты недели:**\n\n"
    for i, row in enumerate(rows, 1):
        short_text = row['text'][:150] + "..." if len(row['text']) > 150 else row['text']
        views = row['views'] or 0
        reactions = row['reactions'] or 0
        if row['message_id'] and (views == 0 and reactions == 0):
            try:
                resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMessageStatistics", params={"chat_id": TELEGRAM_CHAT_ID, "message_id": row['message_id']}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('ok'):
                        stats = data.get('result', {})
                        views = stats.get('views', 0)
                        reactions = sum(r.get('count', 0) for r in stats.get('reactions', []))
                        execute_query('UPDATE posts SET views = ?, reactions = ? WHERE message_id = ?', (views, reactions, row['message_id']))
            except:
                pass
        digest += f"{i}. {short_text}\n   👁 {views} просмотров, ❤️ {reactions} реакций\n\n"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": digest, "parse_mode": "Markdown"}, timeout=30)

# ======================== ОБРАБОТЧИК КНОПОК =========================
edit_mode = {}

def process_callback(callback_data, chat_id, message_id):
    action, session_id = callback_data.split('_', 1)
    print(f"[DEBUG] Callback: {action}, {session_id}")

    if action == "rate_up":
        execute_query('UPDATE posts SET rating = rating + 1 WHERE session_id = ?', (session_id,))
        answer_callback(chat_id, message_id, "Спасибо за оценку! 👍")
        return
    elif action == "rate_down":
        execute_query('UPDATE posts SET rating = rating - 1 WHERE session_id = ?', (session_id,))
        answer_callback(chat_id, message_id, "Спасибо за оценку! 👎")
        return

    post_data = get_post(session_id)
    if not post_data:
        answer_callback(chat_id, message_id, "🔄 Черновик устарел, генерирую новый...")
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_img = generate_image(new_prompt)
            if not new_img:
                send_for_approval_no_image(new_text, new_topic)
                answer_callback(chat_id, message_id, "✅ Новый пост отправлен (без картинки)")
                return
            new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic)
            answer_callback(chat_id, message_id, "✅ Новый пост отправлен на проверку.")
        except Exception as e:
            answer_callback(chat_id, message_id, f"❌ Ошибка: {str(e)[:100]}")
        return

    if post_data["status"] in ("published", "rejected", "approved"):
        answer_callback(chat_id, message_id, f"ℹ️ Пост уже {post_data['status']}.")
        return

    if action == "approve":
        schedule_publish(session_id)
        answer_callback(chat_id, message_id, "✅ Пост одобрен, будет опубликован в 10:00 МСК.")
    elif action == "regenerate":
        answer_callback(chat_id, message_id, "🔄 Генерирую новый...")
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_img = generate_image(new_prompt)
            if not new_img:
                send_for_approval_no_image(new_text, new_topic)
                delete_post(session_id)
                answer_callback(chat_id, message_id, "🔄 Новый пост отправлен (без картинки)")
                return
            new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            delete_post(session_id)
            send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic)
            answer_callback(chat_id, message_id, "🔄 Новый пост отправлен.")
        except Exception as e:
            answer_callback(chat_id, message_id, f"❌ Ошибка: {str(e)[:100]}")
    elif action == "edit":
        answer_callback(chat_id, message_id, "✏️ Пришли новый текст поста (без картинки).")
        edit_mode[chat_id] = session_id
    elif action == "reject":
        update_post_status(session_id, 'rejected')
        answer_callback(chat_id, message_id, "❌ Пост отклонён.")

def answer_callback(chat_id, message_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

# ======================== ПОЛЛИНГ =========================
def poll_updates():
    offset = 0
    while True:
        try:
            resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", params={"offset": offset, "timeout": 30, "allowed_updates": ["callback_query", "message"]}, timeout=35)
            if resp.status_code != 200:
                time.sleep(5)
                continue
            data = resp.json()
            if not data.get("ok"):
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
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "Обрабатываю..."}, timeout=10)
                        except:
                            pass
                elif "message" in update and update["message"].get("chat", {}).get("id") == int(ADMIN_CHAT_ID):
                    chat_id = update["message"]["chat"]["id"]
                    if chat_id in edit_mode:
                        session_id = edit_mode.pop(chat_id)
                        new_text = update["message"].get("text")
                        if new_text:
                            update_post_text(session_id, new_text)
                            post_data = get_post(session_id)
                            if post_data:
                                new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
                                save_post(new_sid, new_text, post_data["image_path"], post_data["image_prompt"], post_data["topic"])
                                delete_post(session_id)
                                send_for_approval(new_text, post_data["image_path"], post_data["image_prompt"], new_sid, post_data["topic"])
                                send_message(chat_id, "✅ Пост обновлён и отправлен на повторную проверку.")
        except Exception as e:
            print(f"[ERROR] poll_updates: {e}")
            time.sleep(5)

# ======================== ПУБЛИКАЦИЯ ЗАПЛАНИРОВАННЫХ =========================
def publish_scheduled_posts():
    print(f"[{datetime.now()}] Проверка запланированных постов...")
    posts = get_approved_posts_to_publish()
    for p in posts:
        if publish_to_telegram(p["text"], p["image_path"], p["session_id"]):
            update_post_status(p["session_id"], 'published')
            print(f"[{datetime.now()}] ✅ Опубликован {p['session_id']}")
        else:
            if publish_text_only(p["text"]):
                update_post_status(p["session_id"], 'published')
                print(f"[{datetime.now()}] ✅ Опубликован текст {p['session_id']}")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка публикации {p['session_id']}")

# ======================== ОТЧЁТЫ =========================
def weekly_report():
    stats = get_weekly_stats()
    if stats:
        msg = f"📊 Еженедельный отчёт:\nОдобрено: {stats['published']}\nОтклонено: {stats['rejected']}\nВсего создано: {stats['total']}"
    else:
        msg = "📊 Недостаточно данных."
    send_message(ADMIN_CHAT_ID, msg)

# ======================== ОСНОВНАЯ ЗАДАЧА =========================
def job(auto_publish=False):
    check_and_repost()
    print(f"[{datetime.now()}] Генерация поста...")
    try:
        post_text, image_prompt, topic = generate_post()
        image_path = generate_image(image_prompt)
        if not image_path:
            print("[WARN] Картинка не сгенерирована, публикую только текст")
            if auto_publish:
                publish_text_only(post_text)
                print(f"[{datetime.now()}] ✅ Пост без картинки опубликован (авто)")
            else:
                send_for_approval_no_image(post_text, topic)
            return

        if auto_publish:
            if publish_to_telegram(post_text, image_path):
                print(f"[{datetime.now()}] ✅ Пост опубликован (авто)")
                send_message(ADMIN_CHAT_ID, f"✅ Авто-пост опубликован в {datetime.now().strftime('%H:%M')}")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка авто-публикации")
        else:
            session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
            ok = send_for_approval(post_text, image_path, image_prompt, session_id, topic)
            if ok:
                print(f"[{datetime.now()}] ✅ Пост отправлен на модерацию")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка модерации")
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
                job(auto_publish=False)
                output = sys.stdout.getvalue()
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(f"✅ Успешно (модерация)!\n\n{output}".encode())
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
        elif self.path == '/test_publish':
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                job(auto_publish=True)
                output = sys.stdout.getvalue()
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(f"✅ Успешно (авто-публикация)!\n\n{output}".encode())
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
            self.wfile.write(b"OK")

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=start_health_server, daemon=True).start()

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
threading.Thread(target=poll_updates, daemon=True).start()

schedule.every().day.at("06:55").do(lambda: job(auto_publish=False))
schedule.every().day.at("07:00").do(publish_scheduled_posts)
schedule.every().sunday.at("17:00").do(weekly_report)
schedule.every().sunday.at("17:00").do(digest_job)

print("Бот запущен. Ожидание расписания...")
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}")

while True:
    schedule.run_pending()
    time.sleep(60)