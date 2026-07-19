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

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

API_PROVIDER = os.getenv("API_PROVIDER", "openrouter").lower()
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek/deepseek-chat:free")

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# ======================== ТЕМЫ ПО ДНЯМ (запасной вариант) =========================
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

# ======================== RSS-ФУНКЦИЯ (запасной вариант) =========================
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

# ======================== ФУНКЦИЯ АНАЛИТИКИ ТЕМ =========================
def get_topic_by_analytics():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT topic, rating, views, reactions FROM posts WHERE status = "published" AND published_at >= ? AND topic IS NOT NULL AND topic != ""',
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
        'SELECT session_id, text, image_path FROM posts WHERE status = "approved" AND scheduled_publish_time <= ?',
        (now,), fetch=True
    )
    return rows

def get_weekly_stats():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT COUNT(*) as total, SUM(CASE WHEN status="published" THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status="rejected" THEN 1 ELSE 0 END) as rejected FROM posts WHERE created_at >= ?',
        (week_ago,), fetchone=True
    )
    return rows

# ======================== ВСПОМОГАТЕЛЬНЫЕ =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

def split_text(text, max_len=3000):
    if len(text) <= max_len:
        return [text]
    parts = []
    while len(text) > max_len:
        chunk = text[:max_len]
        last_punct = max(chunk.rfind('.'), chunk.rfind('!'), chunk.rfind('?'))
        if last_punct > 0:
            split_pos = last_punct + 1
        else:
            last_space = chunk.rfind(' ')
            split_pos = last_space if last_space > 0 else max_len
        parts.append(text[:split_pos].strip())
        text = text[split_pos:].strip()
    if text:
        parts.append(text)
    return parts

def create_fallback_image():
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (1200, 800), color='black')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 60)
        except:
            font = ImageFont.load_default()
        draw.text((100, 380), "Изображение временно недоступно", fill='white', font=font)
        img.save("fallback.jpg")
        print("[DEBUG] Создана заглушка fallback.jpg")
    except Exception as e:
        print(f"[WARN] Не удалось создать заглушку: {e}")
        open("fallback.jpg", "a").close()

# ======================== ГЕНЕРАЦИЯ КАРТИНКИ (без Cloudflare) =========================
def generate_image(prompt, max_attempts=2):
    if len(prompt) > 150:
        prompt = prompt[:150]

    services = [
        {
            "name": "Pollinations",
            "url": lambda p: f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p + str(random.randint(1,100000)))}?width=1200&height=800&seed={random.randint(1,999999)}&t={int(time.time())}",
            "timeout": 90
        },
        {
            "name": "Lexica",
            "url": lambda p: f"https://lexica.art/api/v1/search?q={urllib.parse.quote(p)}",
            "timeout": 30,
            "parse": lambda data: data.get("images", [{}])[0].get("src") if data.get("images") else None
        },
        {
            "name": "Fallback",
            "local": True,
            "path": "fallback.jpg"
        }
    ]

    for service in services:
        try:
            if service.get("local"):
                if os.path.exists(service["path"]):
                    return service["path"]
                create_fallback_image()
                return "fallback.jpg"

            name = service["name"]
            print(f"[DEBUG] Пробуем {name}...")
            url = service["url"](prompt) if callable(service["url"]) else service["url"]
            resp = requests.get(url, timeout=service.get("timeout", 60))

            if resp.status_code == 200:
                if "parse" in service:
                    img_url = service["parse"](resp.json())
                    if img_url:
                        img_resp = requests.get(img_url, timeout=30)
                        if img_resp.status_code == 200:
                            with open("temp_image.jpg", "wb") as f:
                                f.write(img_resp.content)
                            return "temp_image.jpg"
                else:
                    with open("temp_image.jpg", "wb") as f:
                        f.write(resp.content)
                    return "temp_image.jpg"
            else:
                print(f"[WARN] {name} вернул {resp.status_code}")
        except Exception as e:
            print(f"[WARN] Ошибка {name}: {e}")
        time.sleep(2)

    create_fallback_image()
    return "fallback.jpg"

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
                    "Указывай период и источник.\n"
                    "В конце поста, после Action Item, добавь ссылку на источник.\n"
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
                first_sentence = post_text.split('.')[0] if '.' in post_text else post_text[:50]
                image_prompt = f"{first_sentence}, business finance illustration, sarcastic, modern"
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

# ======================== ПУБЛИКАЦИЯ =========================
def publish_text_only(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        resp = requests.post(url, json=data, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Ошибка: {e}")
        return False

def send_for_approval_no_image(post_text, topic):
    session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
    save_post(session_id, post_text, "", "", topic)
    full_parts = split_text(post_text, max_len=3000)
    print(f"[DEBUG] Без картинки, разбито на {len(full_parts)} частей")
    for i, part in enumerate(full_parts, 1):
        text_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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
        resp = requests.post(text_url, json=text_data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка части {i}: {resp.text}")
            return False
    return True

def publish_to_telegram(text, image_path, session_id=None):
    try:
        if not os.path.exists(image_path):
            print("[ERROR] Файл картинки не найден")
            return False
        parts = split_text(text, max_len=1000)
        first_part = parts[0] if parts else ""
        second_part = parts[1] if len(parts) > 1 else ""
        check_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember"
        check_params = {"chat_id": TELEGRAM_CHAT_ID, "user_id": "me"}
        check_resp = requests.get(check_url, params=check_params, timeout=10)
        if check_resp.status_code == 200:
            if check_resp.json().get("result", {}).get("status") not in ["administrator", "creator"]:
                print("[ERROR] Бот не администратор")
                return False
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": first_part}
            resp = requests.post(url, files=files, data=data, timeout=30)
            if resp.status_code != 200:
                print(f"[ERROR] Ошибка отправки фото: {resp.text}")
                return False
            if session_id:
                msg_data = resp.json()
                message_id = msg_data.get('result', {}).get('message_id')
                if message_id:
                    execute_query('UPDATE posts SET message_id = ? WHERE session_id = ?', (message_id, session_id))
        if second_part:
            text_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            text_data = {"chat_id": TELEGRAM_CHAT_ID, "text": f"📎 Продолжение:\n\n{second_part}"}
            text_resp = requests.post(text_url, json=text_data, timeout=30)
            if text_resp.status_code != 200:
                print(f"[ERROR] Ошибка продолжения: {text_resp.text}")
                return False
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка публикации: {e}")
        traceback.print_exc()
        return False

def send_for_approval(post_text, image_path, image_prompt, session_id, topic):
    save_post(session_id, post_text, image_path, image_prompt, topic)
    first_part = split_text(post_text, max_len=1000)[0]
    caption = f"📝 Новый пост на проверку (начало):\n\n{first_part}..."
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
                            {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                            {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                        ]
                    ]
                })
            }
            resp = requests.post(url, files=files, data=data, timeout=30)
            if resp.status_code != 200:
                print(f"[ERROR] Ошибка фото: {resp.text}")
                return False
        full_parts = split_text(post_text, max_len=3000)
        for i, part in enumerate(full_parts, 1):
            text_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            text_data = {
                "chat_id": ADMIN_CHAT_ID,
                "text": f"📄 Полный текст поста (часть {i}/{len(full_parts)}):\n\n{part}"
            }
            text_resp = requests.post(text_url, json=text_data, timeout=30)
            if text_resp.status_code != 200:
                print(f"[ERROR] Ошибка полного текста: {text_resp.text}")
                return False
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка модерации: {e}")
        return False

def schedule_publish(session_id):
    now = datetime.now(MOSCOW_TZ)
    publish_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= publish_time:
        publish_time += timedelta(days=1)
    update_post_status(session_id, 'approved', scheduled_time=publish_time)
    msg = f"✅ Пост одобрен и запланирован на {publish_time.strftime('%d.%m.%Y %H:%M')} МСК."
    send_message(ADMIN_CHAT_ID, msg)

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"[ERROR] Ошибка отправки: {e}")

# ======================== АВТОПОВТОР =========================
def check_and_repost():
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    rows = execute_query(
        'SELECT session_id, text FROM posts WHERE status = "published" AND reposted = 0 AND rating >= 3 AND published_at <= ?',
        (cutoff,), fetch=True
    )
    for row in rows:
        if publish_text_only(row['text']):
            execute_query('UPDATE posts SET reposted = 1 WHERE session_id = ?', (row['session_id'],))
            print(f"[DEBUG] Репост {row['session_id']}")

# ======================== ДАЙДЖЕСТ =========================
def digest_job():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT text, rating, message_id, views, reactions FROM posts WHERE status = "published" AND published_at >= ? ORDER BY rating DESC LIMIT 5',
        (week_ago,), fetch=True
    )
    if not rows:
        send_message(ADMIN_CHAT_ID, "📊 За неделю нет постов.")
        return
    digest = "📅 **Лучшие посты недели:**\n\n"
    for i, row in enumerate(rows, 1):
        short_text = row['text'][:150] + "..." if len(row['text']) > 150 else row['text']
        views = row['views'] or 0
        reactions = row['reactions'] or 0
        if row['message_id'] and (views == 0 and reactions == 0):
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMessageStatistics"
                params = {"chat_id": TELEGRAM_CHAT_ID, "message_id": row['message_id']}
                resp = requests.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('ok'):
                        stats = data.get('result', {})
                        views = stats.get('views', 0)
                        reactions_list = stats.get('reactions', [])
                        reactions = sum(r.get('count', 0) for r in reactions_list)
                        execute_query('UPDATE posts SET views = ?, reactions = ? WHERE message_id = ?', (views, reactions, row['message_id']))
            except Exception as e:
                print(f"[WARN] Статистика ошибка: {e}")
        digest += f"{i}. {short_text}\n   👁 {views} просмотров, ❤️ {reactions} реакций\n\n"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": digest, "parse_mode": "Markdown"}
    resp = requests.post(url, json=data, timeout=30)
    if resp.status_code != 200:
        print(f"[ERROR] Ошибка дайджеста: {resp.text}")

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
                print("[OK] Авто-пост без картинки")
            else:
                send_for_approval_no_image(post_text, topic)
            return
        if auto_publish:
            if publish_to_telegram(post_text, image_path):
                print("[OK] Авто-пост опубликован")
                send_message(ADMIN_CHAT_ID, f"✅ Авто-пост опубликован в {datetime.now().strftime('%H:%M')}")
            else:
                print("[ERROR] Ошибка авто-публикации")
        else:
            session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
            ok = send_for_approval(post_text, image_path, image_prompt, session_id, topic)
            if ok:
                print("[OK] Пост на модерации")
            else:
                print("[ERROR] Ошибка модерации")
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
def poll_updates():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 30, "allowed_updates": ["callback_query", "message"]}
            resp = requests.get(url, params=params, timeout=35)
            if resp.status_code != 200:
                print(f"[ERROR] getUpdates ошибка {resp.status_code}")
                time.sleep(5)
                continue
            data = resp.json()
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
                            ans_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                            ans_data = {"callback_query_id": cb["id"], "text": "Обрабатываю..."}
                            requests.post(ans_url, json=ans_data, timeout=10)
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
                                send_message(chat_id, "✅ Пост обновлён")
                            else:
                                send_message(chat_id, "❌ Не удалось найти пост.")
                        else:
                            send_message(chat_id, "❌ Текст не может быть пустым.")
        except Exception as e:
            print(f"[ERROR] poll_updates: {e}")
            time.sleep(5)

# ======================== ОБРАБОТЧИК КНОПОК =========================
edit_mode = {}

def process_callback(callback_data, chat_id, message_id):
    parts = callback_data.split('_', 1)
    if len(parts) != 2:
        return
    action, session_id = parts
    print(f"[DEBUG] Callback: {action}, {session_id}")

    if action == "rate_up":
        execute_query('UPDATE posts SET rating = rating + 1 WHERE session_id = ?', (session_id,))
        answer_callback(chat_id, message_id, "👍")
        return
    elif action == "rate_down":
        execute_query('UPDATE posts SET rating = rating - 1 WHERE session_id = ?', (session_id,))
        answer_callback(chat_id, message_id, "👎")
        return
    if action == "approve_noimg":
        send_message(chat_id, "ℹ️ Используйте 'Перегенерировать' или 'Отклонить'.")
        return

    post_data = get_post(session_id)
    if not post_data:
        answer_callback(chat_id, message_id, "🔄 Черновик устарел, генерирую новый...")
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_img = generate_image(new_prompt)
            if not new_img:
                send_for_approval_no_image(new_text, new_topic)
                answer_callback(chat_id, message_id, "✅ Новый пост (без картинки)")
                return
            new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic)
            answer_callback(chat_id, message_id, "✅ Новый пост на проверку.")
        except Exception as e:
            answer_callback(chat_id, message_id, f"❌ Ошибка: {str(e)[:100]}")
        return

    if post_data["status"] in ("published", "rejected", "approved"):
        answer_callback(chat_id, message_id, f"ℹ️ Пост уже {post_data['status']}.")
        return

    if action == "approve":
        schedule_publish(session_id)
        answer_callback(chat_id, message_id, "✅ Одобрен, будет опубликован в 10:00 МСК.")
    elif action == "regenerate":
        answer_callback(chat_id, message_id, "🔄 Генерирую новый...")
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_img = generate_image(new_prompt)
            if not new_img:
                send_for_approval_no_image(new_text, new_topic)
                delete_post(session_id)
                answer_callback(chat_id, message_id, "🔄 Новый пост (без картинки)")
                return
            new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            delete_post(session_id)
            send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic)
            answer_callback(chat_id, message_id, "🔄 Новый пост отправлен.")
        except Exception as e:
            answer_callback(chat_id, message_id, f"❌ Ошибка: {str(e)[:100]}")
    elif action == "edit":
        answer_callback(chat_id, message_id, "✏️ Пришли новый текст поста.")
        edit_mode[chat_id] = session_id
    elif action == "reject":
        update_post_status(session_id, 'rejected')
        answer_callback(chat_id, message_id, "❌ Отклонён.")

def answer_callback(chat_id, message_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"[ERROR] Ошибка callback: {e}")

# ======================== РАСПИСАНИЕ =========================
schedule.every().day.at("06:55").do(lambda: job(auto_publish=False))
schedule.every().day.at("07:00").do(publish_scheduled_posts)
schedule.every().sunday.at("17:00").do(weekly_report)
schedule.every().sunday.at("17:00").do(digest_job)

def publish_scheduled_posts():
    print(f"[{datetime.now()}] Проверка запланированных...")
    posts = get_approved_posts_to_publish()
    for p in posts:
        if publish_to_telegram(p["text"], p["image_path"], p["session_id"]):
            update_post_status(p["session_id"], 'published')
            print(f"[OK] Опубликован {p['session_id']}")
        else:
            if publish_text_only(p["text"]):
                update_post_status(p["session_id"], 'published')
                print(f"[OK] Текст {p['session_id']}")
            else:
                print(f"[ERROR] Ошибка {p['session_id']}")

# ======================== ЗАПУСК =========================
threading.Thread(target=poll_updates, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()
threading.Thread(target=start_health_server, daemon=True).start()

print("Бот запущен. Ожидание расписания...")

while True:
    schedule.run_pending()
    time.sleep(60)