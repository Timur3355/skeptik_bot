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
from PIL import Image, ImageStat
import shutil
import sqlite3
from contextlib import closing

# ======================== ПРОВЕРКА POSTGRESQL =========================
pg_available = False
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    pg_available = True
    print("[INFO] psycopg2 загружен", flush=True)
except ImportError:
    print("[WARN] psycopg2 не найден, будет использован SQLite", flush=True)

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")

if not ADMIN_CHAT_ID:
    print("[WARN] ADMIN_CHAT_ID не задан!", flush=True)
    ADMIN_CHAT_ID = None

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
API_PROVIDER = "openai"
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

os.makedirs("images", exist_ok=True)

POST_FORMATS = {
    0: "новость", 1: "мем", 2: "новость", 3: "мем",
    4: "новость", 5: "мем", 6: "аналитика"
}

PROVIDER_CONFIG = {
    "openai": {
        "url": "https://api.chatanywhere.tech/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "headers": lambda key: {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    }
}

config = PROVIDER_CONFIG["openai"]
API_URL = config["url"]
API_HEADERS_FUNC = config["headers"]
API_DEFAULT_MODEL = config["default_model"]
if not MODEL_NAME:
    MODEL_NAME = API_DEFAULT_MODEL

# ======================== RSS ИСТОЧНИКИ =========================
RSS_URLS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/business/rss",
    "https://rss.cnn.com/rss/edition_world.rss",
    "https://www.wired.com/feed/rss",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://news.ycombinator.com/rss",
    "https://www.reddit.com/r/nottheonion/.rss",
    "https://www.rbc.ru/rss/",
    "https://lenta.ru/rss/news",
    "https://www.kommersant.ru/RSS/news.xml",
]

VIRAL_POSITIVE_KEYWORDS = [
    "absurd", "shocking", "bizarre", "weird", "fail", "scandal", "outrage",
    "ridiculous", "hilarious", "embarrassing", "billion", "million",
    "lawsuit", "fired", "resigned", "leaked", "hacked", "crashed",
    "ban", "banned", "refused", "denied", "clashed", "insult",
    "weirdest", "strangest", "caught", "exposed", "scam", "fraud",
    "абсурд", "скандал", "провал", "утечка", "уволили", "штраф",
    "запрет", "обвинили", "разоблачили", "сократили", "упал", "взорвал",
]

VIRAL_NEGATIVE_KEYWORDS = [
    "meeting", "summit", "agreement", "report", "quarterly", "forecast",
    "обсудили", "заседание", "совещание", "прогноз", "план развития",
]

# ======================== БАЗА ДАННЫХ =========================
db_type = None
DB_PATH = "posts.db"

def get_db_connection():
    if db_type == 'postgres' and pg_available:
        try:
            return psycopg2.connect(DATABASE_URL, sslmode='require')
        except Exception as e:
            print(f"[ERROR] PostgreSQL connection failed: {e}", flush=True)
            return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    global db_type, pg_available
    if DATABASE_URL and pg_available:
        try:
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY, session_id TEXT UNIQUE, text TEXT,
                image_path TEXT, image_prompt TEXT, topic TEXT,
                status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP, scheduled_publish_time TIMESTAMP,
                published_at TIMESTAMP, edit_pending BOOLEAN DEFAULT FALSE,
                rating INTEGER DEFAULT 0, reposted BOOLEAN DEFAULT FALSE,
                message_id BIGINT, views INTEGER DEFAULT 0,
                reactions INTEGER DEFAULT 0, format TEXT DEFAULT 'новость')''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posts(topic)')
            cur.execute('CREATE TABLE IF NOT EXISTS prompts (name TEXT PRIMARY KEY, content TEXT)')
            cur.execute('''CREATE TABLE IF NOT EXISTS publish_times (
                id SERIAL PRIMARY KEY, post_id INTEGER, publish_hour INTEGER,
                publish_weekday INTEGER, views INTEGER, reactions INTEGER)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS series (
                name TEXT PRIMARY KEY, last_episode INTEGER DEFAULT 0)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS used_images (
                image_id TEXT PRIMARY KEY,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            default_prompt = (
                "Ты — автор юмористического новостного канала.\n"
                "Канал публикует СВЕЖИЕ мировые новости в саркастично-шутливом формате: бизнес, технологии, экономика, стартапы, крупные компании, знаменитости, абсурдные события по всему миру.\n"
                "Фокус — новости со всего мира (США, Европа, Азия, Россия — все регионы), главное чтобы они были СВЕЖИМИ (последние 1–3 дня).\n"
                "Стиль: дерзкий, ироничный, с шутками и неожиданными сравнениями. Пиши так, будто рассказываешь другу смешную новость за кофе.\n"
                "ОБЯЗАТЕЛЬНО используй эмодзи в каждом абзаце (минимум 3–4 разных).\n"
                "Начинай пост с яркого заголовка с эмодзи.\n"
                "Добавляй ёмкие шутки, сарказм и неожиданные метафоры (например, 'нейросеть? нет, ночная смена').\n"
                "Структура: заголовок → суть новости → развитие сюжета с шутками → неожиданный поворот или финальная ирония.\n"
                "НЕ ДЕЛАЙ блок 'вывод' или 'Action Item' — просто заканчивай пост сильной шуткой или ироничным наблюдением.\n"
                "Не используй шаблонные фразы, будь оригинальным.\n"
                "Используй ТОЛЬКО свежие новости (последние 1–3 дня).\n"
                "Пост должен быть 700–1000 символов (7–9 предложений). ОБЯЗАТЕЛЬНО заканчивай точкой, восклицанием или вопросом.\n"
                "Ключевые цифры выделяй жирным через HTML-тег <b>...</b> (НЕ используй **).\n"
                "После текста — источник (если неизвестен, укажи 'по данным открытых источников') и хештеги (#тег1 #тег2).\n"
                "Не используй разделители вроде '---'.\n"
                "После текста === и описание картинки на английском (5–7 слов), "
                "обязательно с no text, no letters, no words, no captions, no watermark."
            )
            cur.execute('INSERT INTO prompts (name, content) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING', ('system_prompt', default_prompt))
            conn.commit(); cur.close(); conn.close()
            db_type = 'postgres'; pg_available = True
            print("[INFO] Подключение к PostgreSQL успешно.", flush=True)
            return
        except Exception as e:
            print(f"[WARN] Ошибка PostgreSQL: {e}. Переключаюсь на SQLite.", flush=True)
            db_type = None; pg_available = False

    db_type = 'sqlite'
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT UNIQUE, text TEXT,
            image_path TEXT, image_prompt TEXT, topic TEXT,
            status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP, scheduled_publish_time TIMESTAMP,
            published_at TIMESTAMP, edit_pending INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 0, reposted INTEGER DEFAULT 0,
            message_id INTEGER, views INTEGER DEFAULT 0,
            reactions INTEGER DEFAULT 0, format TEXT DEFAULT 'новость')''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posts(topic)')
        conn.execute('CREATE TABLE IF NOT EXISTS prompts (name TEXT PRIMARY KEY, content TEXT)')
        conn.execute('''CREATE TABLE IF NOT EXISTS publish_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER,
            publish_hour INTEGER, publish_weekday INTEGER,
            views INTEGER, reactions INTEGER,
            FOREIGN KEY(post_id) REFERENCES posts(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS series (
            name TEXT PRIMARY KEY, last_episode INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS used_images (
            image_id TEXT PRIMARY KEY,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        default_prompt_sqlite = (
            "Ты — автор юмористического новостного канала.\n"
            "Канал публикует СВЕЖИЕ мировые новости в саркастично-шутливом формате: бизнес, технологии, экономика, стартапы, крупные компании, знаменитости, абсурдные события по всему миру.\n"
            "Фокус — новости со всего мира (США, Европа, Азия, Россия — все регионы), главное чтобы они были СВЕЖИМИ (последние 1–3 дня).\n"
            "Стиль: дерзкий, ироничный, с шутками и неожиданными сравнениями. Пиши так, будто рассказываешь другу смешную новость за кофе.\n"
            "ОБЯЗАТЕЛЬНО используй эмодзи в каждом абзаце (минимум 3–4 разных).\n"
            "Начинай пост с яркого заголовка с эмодзи.\n"
            "Добавляй ёмкие шутки, сарказм и неожиданные метафоры (например, 'нейросеть? нет, ночная смена').\n"
            "Структура: заголовок → суть новости → развитие сюжета с шутками → неожиданный поворот или финальная ирония.\n"
            "НЕ ДЕЛАЙ блок 'вывод' или 'Action Item' — просто заканчивай пост сильной шуткой или ироничным наблюдением.\n"
            "Не используй шаблонные фразы, будь оригинальным.\n"
            "Используй ТОЛЬКО свежие новости (последние 1–3 дня).\n"
            "Пост должен быть 700–1000 символов (7–9 предложений). ОБЯЗАТЕЛЬНО заканчивай точкой, восклицанием или вопросом.\n"
            "Ключевые цифры выделяй жирным через HTML-тег <b>...</b> (НЕ используй **).\n"
            "После текста — источник (если неизвестен, укажи 'по данным открытых источников') и хештеги (#тег1 #тег2).\n"
            "Не используй разделители вроде '---'.\n"
            "После текста === и описание картинки на английском (5–7 слов), "
            "обязательно с no text, no letters, no words, no captions, no watermark."
        )
        conn.execute('INSERT OR IGNORE INTO prompts (name, content) VALUES (?, ?)', ('system_prompt', default_prompt_sqlite))
        conn.commit()
    print("[INFO] Используется SQLite.", flush=True)

init_db()

def execute_query(query, params=None, fetch=False, fetchone=False):
    if db_type == 'postgres' and pg_available:
        query = query.replace('?', '%s')
        conn = get_db_connection()
        if conn is None:
            return execute_query_sqlite(query, params, fetch, fetchone)
        cur = conn.cursor(cursor_factory=RealDictCursor if fetch or fetchone else None)
        cur.execute(query, params or ())
        if fetch: result = cur.fetchall()
        elif fetchone: result = cur.fetchone()
        else: result = None
        conn.commit(); cur.close(); conn.close()
        return result
    return execute_query_sqlite(query, params, fetch, fetchone)

def execute_query_sqlite(query, params=None, fetch=False, fetchone=False):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, params or ())
        if fetch: result = [dict(row) for row in cur.fetchall()]
        elif fetchone:
            row = cur.fetchone()
            result = dict(row) if row else None
        else: result = None
        conn.commit()
        return result

def get_prompt():
    row = execute_query('SELECT content FROM prompts WHERE name = ?', ('system_prompt',), fetchone=True)
    return row['content'] if row else None

def set_prompt(content):
    if db_type == 'postgres' and pg_available:
        execute_query('INSERT INTO prompts (name, content) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET content = EXCLUDED.content', ('system_prompt', content))
    else:
        execute_query('REPLACE INTO prompts (name, content) VALUES (?, ?)', ('system_prompt', content))

# ======================== CRUD ПОСТОВ =========================
def save_post(session_id, text, image_path, image_prompt, topic, format_type):
    if db_type == 'postgres' and pg_available:
        query = '''INSERT INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at, format)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET text=EXCLUDED.text, image_path=EXCLUDED.image_path,
                image_prompt=EXCLUDED.image_prompt, topic=EXCLUDED.topic, status=EXCLUDED.status,
                created_at=EXCLUDED.created_at, format=EXCLUDED.format'''
        params = (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat(), format_type)
    else:
        query = 'INSERT OR REPLACE INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at, format) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
        params = (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat(), format_type)
    execute_query(query, params)

def get_post(session_id):
    return execute_query('SELECT text, image_path, image_prompt, status, scheduled_publish_time, edit_pending, rating, reposted, message_id, topic, format FROM posts WHERE session_id = ?', (session_id,), fetchone=True)

def update_post_status(session_id, status, scheduled_time=None):
    if scheduled_time:
        execute_query('UPDATE posts SET status = ?, scheduled_publish_time = ?, approved_at = ? WHERE session_id = ?',
                      (status, scheduled_time.isoformat(), datetime.now().isoformat(), session_id))
    else:
        execute_query('UPDATE posts SET status = ? WHERE session_id = ?', (status, session_id))

def delete_post(session_id):
    execute_query('DELETE FROM posts WHERE session_id = ?', (session_id,))

def get_last_posts(limit=5):
    return execute_query('SELECT topic, status, created_at, text, format FROM posts ORDER BY created_at DESC LIMIT ?', (limit,), fetch=True)

def get_monthly_top(limit=5):
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()
    return execute_query(
        'SELECT topic, text, views, reactions FROM posts WHERE status = \'published\' AND published_at >= ? ORDER BY views DESC LIMIT ?',
        (month_ago, limit), fetch=True
    )

# ======================== ОБРАБОТКА ТЕКСТА =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

def beautify_post(text):
    if not text: return ""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\*\*', '', text)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    paragraphs = []
    i = 0
    while i < len(sentences):
        if i + 1 < len(sentences):
            paragraphs.append(sentences[i] + ' ' + sentences[i + 1]); i += 2
        else:
            paragraphs.append(sentences[i]); i += 1
    text = '\n\n'.join(paragraphs)
    def replacer(m):
        num = m.group(0)
        if not re.search(r'<b>.*?' + re.escape(num) + r'.*?</b>', text):
            return f'<b>{num}</b>'
        return num
    text = re.sub(r'\b(\d+[.,]?\d*)\b', replacer, text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text

def split_into_parts(text, max_len=1000):
    if len(text) <= max_len: return [text]
    paragraphs = text.split('\n\n')
    result_parts = []; current_part = ""
    for para in paragraphs:
        if not para.strip(): continue
        if len(current_part) + len(para) + 2 <= max_len:
            current_part = (current_part + '\n\n' + para) if current_part else para
        else:
            if len(para) > max_len:
                sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
                for sent in sentences:
                    if len(current_part) + len(sent) + 2 <= max_len:
                        current_part = (current_part + ' ' + sent) if current_part else sent
                    else:
                        if current_part: result_parts.append(current_part)
                        current_part = sent
            else:
                if current_part: result_parts.append(current_part)
                current_part = para
    if current_part: result_parts.append(current_part)
    return result_parts if result_parts else [text[:max_len] + "..."]

# ======================== БЕЗОПАСНЫЙ API =========================
def safe_api_call(payload, max_attempts=3, base_delay=3):
    headers = API_HEADERS_FUNC(DEEPSEEK_API_KEY)
    for attempt in range(max_attempts):
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                data = r.json()
                if "choices" in data and data["choices"]:
                    content = data["choices"][0]["message"]["content"]
                    if content and len(content.strip()) > 20:
                        return content
            elif r.status_code in (429, 500, 502, 503, 504):
                wait = base_delay * (2 ** attempt)
                print(f"[API] {r.status_code}, ждём {wait}с", flush=True)
                time.sleep(wait); continue
            else:
                print(f"[API] {r.status_code}: {r.text[:200]}", flush=True)
        except requests.exceptions.Timeout:
            print(f"[API] Timeout, попытка {attempt+1}", flush=True)
            time.sleep(base_delay)
        except Exception as e:
            print(f"[API] {e}", flush=True)
            time.sleep(base_delay)
    return None

def validate_digest(text):
    if not text or len(text) < 100: return False
    if len(text) > 700: return False
    low_start = text.lower()[:80]
    if any(x in low_start for x in ["доброе утро", "всем привет", "привет,", "привет!"]):
        print("[VALIDATE] Дайджест начинается с приветствия — отклонён", flush=True)
        return False
    bad = ["===", "<think>", "API временно", "недоступен", "as an ai", "language model", "извините"]
    for m in bad:
        if m.lower() in text.lower(): return False
    if not (bool(re.search(r'[1-3][️⃣\.\)]', text)) or text.count('\n\n') >= 2): return False
    return True

def clean_poll_text(text, max_len=95):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[«»""]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len-3].rstrip() + "..."
    return text

# ======================== ЗАЩИТА ОТ ПОВТОРОВ КАРТИНОК =========================
def is_image_used(image_id):
    if not image_id: return False
    row = execute_query('SELECT image_id FROM used_images WHERE image_id = ?', (image_id,), fetchone=True)
    return row is not None

def mark_image_used(image_id):
    if not image_id: return
    try:
        if db_type == 'postgres' and pg_available:
            execute_query('INSERT INTO used_images (image_id) VALUES (%s) ON CONFLICT (image_id) DO NOTHING', (image_id,))
        else:
            execute_query('INSERT OR IGNORE INTO used_images (image_id) VALUES (?)', (image_id,))
    except Exception as e:
        print(f"[USED_IMG] {e}", flush=True)

# ======================== КАРТИНКИ =========================
def search_image_unsplash(query, max_attempts=3):
    if not UNSPLASH_ACCESS_KEY: return None
    extra = ["cartoon", "satire", "editorial", "chaos", "world", "protest", "news", "dramatic"]
    search_query = f"{query} {random.choice(extra)}"
    for attempt in range(max_attempts):
        try:
            url = "https://api.unsplash.com/search/photos"
            params = {"query": search_query, "per_page": 15, "orientation": "landscape", "content_filter": "high"}
            headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if results:
                    random.shuffle(results)
                    for item in results:
                        img_id = item.get("id", "")
                        if is_image_used(img_id):
                            continue
                        img_url = item["urls"]["regular"]
                        ir = requests.get(img_url, timeout=30)
                        if ir.status_code == 200 and len(ir.content) > 10000:
                            os.makedirs("images", exist_ok=True)
                            unique = f"images/img_{int(time.time())}_{random.randint(1000,9999)}.jpg"
                            with open(unique, "wb") as f:
                                f.write(ir.content)
                            mark_image_used(img_id)
                            return unique
            time.sleep(1)
        except Exception as e:
            print(f"[ERROR] Unsplash: {e}", flush=True)
        time.sleep(2)
    return None

def generate_image(prompt):
    if UNSPLASH_ACCESS_KEY:
        p = search_image_unsplash(prompt, max_attempts=3)
        if p: return p
    print("[WARN] Pollinations", flush=True)
    no_text = (
        "no text, no letters, no words, no captions, no labels, no signs, "
        "no watermark, no logo, no signature, no typography, no writing, "
        "no speech bubbles, clean image, visual only"
    )
    enhanced = (
        f"{prompt}, {no_text}, "
        f"high quality, 8k, sharp focus, detailed, professional, "
        f"vibrant colors, editorial cartoon style, cinematic lighting"
    )
    if len(enhanced) > 380: enhanced = enhanced[:380]
    for attempt in range(3):
        try:
            random_style = random.choice([
                "dramatic lighting", "chaotic composition", "bold colors",
                "cinematic shadows", "high contrast", "energetic mood",
                "volumetric light", "sharp details", "vibrant tones"
            ])
            full = f"{enhanced}, {random_style}, variant {random.randint(1, 999999)}"
            encoded = urllib.parse.quote(full)
            seed = random.randint(1, 9999999)
            ts = int(time.time())
            url = f"https://image.pollinations.ai/prompt/{encoded}?width=1920&height=1080&seed={seed}&nologo=true&t={ts}"
            r = requests.get(url, timeout=90)
            if r.status_code == 200 and len(r.content) > 1000:
                os.makedirs("images", exist_ok=True)
                unique = f"images/img_{int(time.time())}_{random.randint(1000,9999)}.jpg"
                with open(unique, "wb") as f: f.write(r.content)
                return unique
        except Exception as e:
            print(f"[WARN] Pollinations: {e}", flush=True)
        time.sleep(3)
    return None

def validate_image(path):
    try:
        if not path or not os.path.exists(path): return False
        if os.path.getsize(path) < 30000: return False
        img = Image.open(path).convert('L')
        if img.width < 800 or img.height < 600: return False
        stat = ImageStat.Stat(img)
        if stat.stddev[0] < 8: return False
        return True
    except Exception as e:
        print(f"[VALIDATE] {e}", flush=True)
        return False

def generate_image_strict(prompt, max_attempts=4):
    for attempt in range(max_attempts):
        p = generate_image(prompt)
        if p and validate_image(p): return p
        if p:
            try: os.remove(p)
            except: pass
        print(f"[STRICT] Попытка {attempt+1}/{max_attempts} не прошла", flush=True)
    return None

# ======================== СВЕЖАЯ НОВОСТЬ =========================
def get_fresh_news_for_post():
    """Собирает свежие новости из RSS и выбирает случайную, которой не было в последних постах."""
    candidates = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.title.strip()
                summary = re.sub(r'<[^>]+>', '', entry.get('summary', ''))[:250]
                if len(title) < 20:
                    continue
                published = entry.get('published', '')
                if published:
                    try:
                        pub_date = None
                        for fmt in ('%a, %d %b %Y %H:%M:%S %Z', '%a, %d %b %Y %H:%M:%S %z'):
                            try:
                                pub_date = datetime.strptime(published[:25], fmt)
                                break
                            except:
                                continue
                        if pub_date and (datetime.now() - pub_date.replace(tzinfo=None)).days > 2:
                            continue
                    except:
                        pass
                candidates.append({'title': title, 'summary': summary})
        except Exception as e:
            print(f"[NEWS] RSS {url}: {e}", flush=True)

    if not candidates:
        return None

    three_days_ago = (datetime.now() - timedelta(days=3)).isoformat()
    recent = execute_query(
        "SELECT topic FROM posts WHERE created_at >= ? AND topic IS NOT NULL",
        (three_days_ago,), fetch=True
    ) or []
    recent_titles = set()
    for r in recent:
        t = (r['topic'] or "").lower()
        if t:
            recent_titles.add(t[:40])

    fresh = [c for c in candidates if c['title'].lower()[:40] not in recent_titles]
    pool = fresh if fresh else candidates
    chosen = random.choice(pool)
    print(f"[NEWS] Выбрана: {chosen['title'][:80]}", flush=True)
    return f"{chosen['title']}. {chosen['summary']}"

# ======================== ГЕНЕРАЦИЯ ПОСТА =========================
def generate_post(custom_topic=None):
    if custom_topic:
        topic = custom_topic
    else:
        topic = get_fresh_news_for_post()
        if not topic:
            topic = "самая свежая абсурдная новость из мира за последние сутки"

    format_type = POST_FORMATS.get(datetime.now().weekday(), "новость")
    system_prompt = get_prompt()
    format_style = {
        "мем": "Сделай пост с юмором и сарказмом, но не короче 600 символов.",
        "новость": "Информативный пост с фактами, датами и цифрами, не короче 700 символов.",
        "аналитика": "Глубокий разбор с иронией, не короче 700 символов."
    }.get(format_type, "")

    user_prompt = f"Напиши пост на тему: {topic}. {format_style} Используй свежие новости."
    payload = {"model": MODEL_NAME, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ], "temperature": 0.9, "max_tokens": 1500}

    raw = safe_api_call(payload, max_attempts=3)
    if not raw:
        return None, None, None, None

    full_text = clean_text(raw)
    if full_text and full_text[-1] not in ('.', '!', '?'): full_text += '.'

    if "===" in full_text:
        parts = full_text.split("===", 1)
        post_text = parts[0].strip()
        image_prompt = parts[1].strip() if len(parts) > 1 else ""
    else:
        post_text = full_text.strip(); image_prompt = ""

    no_text_suffix = (
        "no text, no letters, no words, no captions, no labels, no signs, "
        "no watermark, no logo, no typography"
    )
    if len(image_prompt) < 10:
        image_prompt = (
            f"humorous news cartoon, {topic[:60]}, satirical, funny, "
            f"dramatic editorial illustration, {no_text_suffix}"
        )
    else:
        image_prompt += f", humorous news cartoon, satirical, dramatic editorial, {no_text_suffix}"
    return beautify_post(post_text), image_prompt, topic, format_type

# ======================== ВИРУСНЫЙ ДЕТЕКТОР =========================
def find_viral_news():
    print("[VIRAL] Сканирую...", flush=True)
    candidates = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:15]:
                title = e.title.strip()
                summary = e.get('summary', '')[:300]
                low = (title + " " + summary).lower()
                score = 0
                for kw in VIRAL_POSITIVE_KEYWORDS:
                    if kw in low: score += 2
                for kw in VIRAL_NEGATIVE_KEYWORDS:
                    if kw in low: score -= 3
                if re.search(r'\$?\d+\s*(billion|million|млрд|млн)', low): score += 3
                if '"' in title or '«' in title: score += 2
                if score >= 4:
                    candidates.append({'title': title, 'summary': summary, 'score': score})
        except: continue
    if not candidates: return None
    candidates.sort(key=lambda x: x['score'], reverse=True)
    best = candidates[0]
    print(f"[VIRAL] {best['title']} ({best['score']})", flush=True)
    return best

def generate_viral_post():
    news = find_viral_news()
    if not news:
        send_message(ADMIN_CHAT_ID, "😔 Вирусных новостей не нашлось.")
        return None
    prompt = (
        "Ты — автор юмористического новостного канала, специализирующийся на САМЫХ АБСУРДНЫХ мировых новостях.\n"
        "Стиль: дерзкий, ироничный, с неожиданными метафорами (как 'нейросеть? нет, ночная смена').\n"
        "Структура: яркий заголовок с эмодзи → суть → развитие с сарказмом → финальная ирония.\n"
        "НЕ ДЕЛАЙ вывод. Эмодзи в каждом абзаце. Ключевые цифры — <b>...</b>.\n"
        "700–1000 символов. Источник + 3-4 хештега.\n"
        "После === описание картинки на английском (5-7 слов) с no text, no letters, no watermark."
    )
    payload = {"model": MODEL_NAME, "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"НОВОСТЬ:\n{news['title']}\n{news['summary']}\n\nСделай вирусный пост."}
    ], "temperature": 0.95, "max_tokens": 1500}
    raw = safe_api_call(payload, max_attempts=3)
    if not raw: return None
    full_text = clean_text(raw)
    if "===" in full_text:
        parts = full_text.split("===", 1)
        post_text = parts[0].strip()
        image_prompt = parts[1].strip() if len(parts) > 1 else ""
    else:
        post_text = full_text.strip(); image_prompt = ""
    no_text = "no text, no letters, no words, no captions, no watermark, no logo"
    if len(image_prompt) < 10:
        image_prompt = f"satirical editorial cartoon, {news['title'][:50]}, humorous, {no_text}"
    else:
        image_prompt += f", satirical editorial cartoon, {no_text}"
    post_text = beautify_post(post_text)
    image_path = generate_image_strict(image_prompt, max_attempts=3)
    session_id = f"viral_{int(time.time())}_{random.randint(1000,9999)}"
    if image_path:
        send_for_approval(post_text, image_path, image_prompt, session_id, f"VIRAL: {news['title'][:80]}", "вирусный")
    else:
        send_for_approval_no_image(post_text, f"VIRAL: {news['title'][:80]}", "вирусный")
    send_message(ADMIN_CHAT_ID, f"🔥 <b>Вирусная новость!</b>\n\n📰 {news['title']}\n💯 Score: {news['score']}")
    return news['title']

def check_urgent_viral():
    print("[URGENT] Проверка...", flush=True)
    news = find_viral_news()
    if not news: return
    if news['score'] >= 6:
        prompt = (
            "Ты — автор юмористического канала. СРОЧНАЯ СЕНСАЦИЯ!\n"
            "Сделай СРОЧНЫЙ пост: 🚨 в заголовке, суть, шутки, финальная ирония.\n"
            "Эмодзи в каждом абзаце. Ключевые цифры — <b>...</b>.\n"
            "700–1000 символов. Источник + хештеги.\n"
            "После === описание картинки на английском с no text, no letters, no watermark."
        )
        payload = {"model": MODEL_NAME, "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"НОВОСТЬ:\n{news['title']}\n{news['summary']}"}
        ], "temperature": 0.95, "max_tokens": 1500}
        raw = safe_api_call(payload, max_attempts=3)
        if not raw: return
        full_text = clean_text(raw)
        no_text = "no text, no letters, no words, no watermark, no logo"
        if "===" in full_text:
            parts = full_text.split("===", 1)
            post_text = parts[0].strip()
            image_prompt = parts[1].strip() if len(parts) > 1 else ""
        else:
            post_text = full_text.strip()
            image_prompt = f"urgent news cartoon, {news['title'][:50]}, dramatic, {no_text}"
        post_text = beautify_post(post_text)
        image_path = generate_image_strict(image_prompt, max_attempts=3)
        session_id = f"urgent_{int(time.time())}_{random.randint(1000,9999)}"
        if image_path:
            send_for_approval(post_text, image_path, image_prompt, session_id, f"🚨 URGENT: {news['title'][:80]}", "срочный")
        else:
            send_for_approval_no_image(post_text, f"🚨 URGENT: {news['title'][:80]}", "срочный")
        send_message(ADMIN_CHAT_ID, f"🚨 <b>СРОЧНАЯ НОВОСТЬ!</b>\n\n{news['title']}\n💯 Score: {news['score']}")

# ======================== УТРЕННИЙ ДАЙДЖЕСТ =========================
def morning_digest():
    print("[DIGEST] Собираю...", flush=True)
    news_list = []
    for url in RSS_URLS[:8]:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:8]:
                title = e.title.strip()
                summary = re.sub(r'<[^>]+>', '', e.get('summary', ''))[:150]
                if len(title) < 15: continue
                low = (title + " " + summary).lower()
                score = 0
                for kw in VIRAL_POSITIVE_KEYWORDS:
                    if kw in low: score += 2
                for kw in VIRAL_NEGATIVE_KEYWORDS:
                    if kw in low: score -= 2
                if re.search(r'\$?\d+\s*(billion|million|млрд|млн)', low): score += 3
                news_list.append({'title': title, 'summary': summary, 'score': score})
        except Exception as e:
            print(f"[DIGEST] RSS {url}: {e}", flush=True)

    if len(news_list) < 3:
        send_message(ADMIN_CHAT_ID, "☀️ Дайджест пропущен: мало новостей")
        return

    seen = set(); unique = []
    for n in sorted(news_list, key=lambda x: x['score'], reverse=True):
        key = n['title'][:40].lower()
        if key not in seen:
            unique.append(n); seen.add(key)
        if len(unique) >= 3: break

    items = "\n".join([f"{i+1}. {n['title']} — {n['summary']}" for i, n in enumerate(unique)])
    system = (
        "Ты — автор юмористического новостного канала. Напиши УТРЕННИЙ ДАЙДЖЕСТ.\n"
        "ВАЖНО: НЕ начинай с приветствия! Никаких 'Доброе утро', 'Привет', 'Всем привет'.\n"
        "Приветствие уже отправлено отдельным сообщением до тебя.\n"
        "Начинай сразу с первой новости.\n"
        "Формат (строго!):\n"
        "1️⃣ [Новость 1 в одну строку] + [одна шутка]\n\n"
        "2️⃣ [Новость 2 в одну строку] + [одна шутка]\n\n"
        "3️⃣ [Новость 3 в одну строку] + [одна шутка]\n\n"
        "Всего 250–450 символов. Эмодзи в каждом блоке.\n"
        "Без хештегов. Без источника. Без ссылок.\n"
        "НЕ используй '===' и 'Action Item'. Только русский."
    )
    payload = {"model": MODEL_NAME, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Топ-3 новости:\n{items}"}
    ], "temperature": 0.85, "max_tokens": 700}

    raw = safe_api_call(payload, max_attempts=3)
    if not raw:
        send_message(ADMIN_CHAT_ID, "⚠️ Дайджест пропущен: API не ответил")
        return
    text = clean_text(raw)
    if "===" in text: text = text.split("===", 1)[0].strip()
    text = re.sub(r'#\w+\s*', '', text).strip()

    if not validate_digest(text):
        send_message(ADMIN_CHAT_ID, f"⚠️ Дайджест не прошёл валидацию:\n\n{text[:300]}")
        return

    if publish_text_only(text):
        send_message(ADMIN_CHAT_ID, f"☀️ <b>Дайджест опубликован:</b>\n\n{text}")
        print("[DIGEST] ✅", flush=True)
    else:
        send_message(ADMIN_CHAT_ID, f"❌ Дайджест не ушёл. Текст:\n\n{text}")

# ======================== ОПРОС НЕДЕЛИ =========================
def weekly_poll():
    print("[POLL] Создаю...", flush=True)
    candidates = []
    for url in RSS_URLS[:10]:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:10]:
                title = e.title.strip()
                if len(title) < 20 or len(title) > 250: continue
                low = (title + " " + e.get('summary','')[:200]).lower()
                score = 0
                for kw in VIRAL_POSITIVE_KEYWORDS:
                    if kw in low: score += 2
                for kw in VIRAL_NEGATIVE_KEYWORDS:
                    if kw in low: score -= 3
                if score >= 4:
                    candidates.append({'title': title, 'score': score})
        except: continue

    if len(candidates) < 3:
        send_message(ADMIN_CHAT_ID, "🗳 Опрос пропущен: мало новостей")
        return

    seen = set(); options = []
    for n in sorted(candidates, key=lambda x: x['score'], reverse=True):
        c = clean_poll_text(n['title'], max_len=95)
        if c and c not in seen and len(c) >= 15:
            options.append(c); seen.add(c)
        if len(options) >= 4: break

    if len(options) < 3:
        send_message(ADMIN_CHAT_ID, "🗳 Опрос пропущен: мало вариантов")
        return

    question = "🗳 Какая новость недели самая АБСУРДНАЯ?"
    if len(question) > 300: question = question[:297] + "..."

    poll_data = {
        "chat_id": TELEGRAM_CHAT_ID, "question": question,
        "options": options, "is_anonymous": True, "allows_multiple_answers": False
    }
    for attempt in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPoll", json=poll_data, timeout=30)
            if r.status_code == 200 and r.json().get('ok'):
                send_message(ADMIN_CHAT_ID, f"🗳 <b>Опрос опубликован!</b>\n\n{question}\n\n" +
                             "\n".join([f"{i+1}. {o}" for i, o in enumerate(options)]))
                print("[POLL] ✅", flush=True)
                return
            else:
                print(f"[POLL] Ошибка: {r.text[:200]}", flush=True)
                if r.status_code == 400: break
                time.sleep(3 * (attempt + 1))
        except Exception as e:
            print(f"[POLL] {e}", flush=True); time.sleep(3)
    send_message(ADMIN_CHAT_ID, "❌ Опрос не ушёл в канал")

# ======================== МЕМ-ПЯТНИЦА =========================
def friday_meme():
    print("[MEME] Генерирую...", flush=True)
    news = find_viral_news()
    if not news:
        send_message(ADMIN_CHAT_ID, "😂 Для мема нет новости"); return
    prompt = (
        "Ты — автор юмористического канала. Пятница — мем!\n"
        "Сделай КОРОТКУЮ мем-подпись: до 200 символов, 2-3 эмодзи, максимум иронии.\n"
        "Структура: заголовок → добивающая шутка.\n"
        "Без хештегов, источника, 'вывод'.\n"
        "После === описание картинки (англ., 3-4 слова) с no text, no letters, no watermark."
    )
    payload = {"model": MODEL_NAME, "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Новость: {news['title']}\n{news['summary']}"}
    ], "temperature": 1.0, "max_tokens": 500}
    raw = safe_api_call(payload, max_attempts=3)
    if not raw: return
    full_text = clean_text(raw)
    no_text = "no text, no letters, no words, no captions, no watermark, no logo"
    if "===" in full_text:
        parts = full_text.split("===", 1)
        post_text = parts[0].strip()
        image_prompt = parts[1].strip() if len(parts) > 1 else ""
    else:
        post_text = full_text.strip(); image_prompt = ""
    if len(image_prompt) < 10:
        image_prompt = f"funny satirical cartoon meme, {news['title'][:50]}, humorous, {no_text}"
    else:
        image_prompt += f", funny satirical cartoon meme, {no_text}"
    image_path = generate_image_strict(image_prompt, max_attempts=4)
    session_id = f"meme_{int(time.time())}_{random.randint(1000,9999)}"
    if image_path:
        send_for_approval(post_text, image_path, image_prompt, session_id, f"MEME: {news['title'][:60]}", "мем")
        send_message(ADMIN_CHAT_ID, "😂 Мем-пятница на модерации")
    else:
        send_for_approval_no_image(post_text, f"MEME (без картинки): {news['title'][:60]}", "мем")
        send_message(ADMIN_CHAT_ID, "⚠️ Картинка не прошла — отправлен только текст")

# ======================== СЕРИЯ "КОГО УБИЛ ИИ" =========================
def next_episode(name):
    row = execute_query('SELECT last_episode FROM series WHERE name = ?', (name,), fetchone=True)
    ep = (row['last_episode'] or 0) + 1 if row else 1
    if db_type == 'postgres' and pg_available:
        execute_query('INSERT INTO series (name, last_episode) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET last_episode = EXCLUDED.last_episode', (name, ep))
    else:
        execute_query('REPLACE INTO series (name, last_episode) VALUES (?, ?)', (name, ep))
    return ep

def ai_killed_series():
    print("[AI-KILLED] Генерирую...", flush=True)
    episode = next_episode("ai_killed")
    professions = [
        "копирайтеры", "переводчики", "младшие программисты", "дизайнеры-иллюстраторы",
        "колл-центры", "ретушёры", "аналитики данных", "корректоры",
        "модераторы контента", "радиоведущие", "секретари", "логисты",
        "бухгалтеры", "тестировщики", "юристы-консультанты", "копирайтинг новостей",
        "переводчики субтитров", "дикторы", "редакторы", "стажёры в консалтинге"
    ]
    profession = professions[(episode - 1) % len(professions)]
    prompt = (
        "Ты — автор юмористического канала. Рубрика 'Кого убил ИИ'.\n"
        "Каждый выпуск — про одну профессию, которую ИИ вытеснил.\n"
        "Формат: заголовок '🤖 Кого убил ИИ. Выпуск N: [Профессия]'.\n"
        "2-3 абзаца — что случилось, реальные примеры, шутка.\n"
        "Финал — саркастичная мысль про следующую жертву.\n"
        "Эмодзи в каждом абзаце. Ключевые цифры — <b>...</b>.\n"
        "700–1000 символов. 3-4 хештега.\n"
        "После === описание картинки (англ., 3-4 слова) с no text, no letters, no watermark."
    )
    payload = {"model": MODEL_NAME, "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Сделай выпуск #{episode} про профессию: {profession}. Реальные цифры 2024-2026."}
    ], "temperature": 0.9, "max_tokens": 1500}
    raw = safe_api_call(payload, max_attempts=3)
    if not raw: return
    full_text = clean_text(raw)
    no_text = "no text, no letters, no words, no watermark, no logo"
    if "===" in full_text:
        parts = full_text.split("===", 1)
        post_text = parts[0].strip()
        image_prompt = parts[1].strip() if len(parts) > 1 else ""
    else:
        post_text = full_text.strip(); image_prompt = ""
    if len(image_prompt) < 10:
        image_prompt = f"robot replacing human worker, satirical cartoon, {profession}, funny, {no_text}"
    post_text = beautify_post(post_text)
    image_path = generate_image_strict(image_prompt, max_attempts=3)
    session_id = f"aikilled_{int(time.time())}_{random.randint(1000,9999)}"
    if image_path:
        send_for_approval(post_text, image_path, image_prompt, session_id, f"AI KILLED #{episode}: {profession}", "серия")
    else:
        send_for_approval_no_image(post_text, f"AI KILLED #{episode}: {profession}", "серия")
    send_message(ADMIN_CHAT_ID, f"🤖 Выпуск #{episode} ({profession}) на модерации")

# ======================== ПРИВЕТСТВИЯ =========================
def generate_greeting(kind):
    if kind == 'morning':
        instr = "Короткое (до 250 символов) шуточное пожелание доброго утра для новостного канала. 2-3 эмодзи, добрый юмор, без хештегов."
    else:
        instr = "Короткое (до 250 символов) шуточное пожелание спокойной ночи для новостного канала. 2-3 эмодзи, добрый юмор, без хештегов."
    payload = {"model": MODEL_NAME, "messages": [
        {"role": "system", "content": "Ты — автор юмористического канала."},
        {"role": "user", "content": instr}
    ], "temperature": 0.9, "max_tokens": 300}
    raw = safe_api_call(payload, max_attempts=3)
    if raw:
        return clean_text(raw)
    return "☀️ Доброе утро! Хорошего дня!" if kind == 'morning' else "🌙 Спокойной ночи!"

def send_morning_greeting():
    publish_text_only(generate_greeting('morning'))
    print(f"[{datetime.now()}] ☀️ Утро", flush=True)

def send_evening_greeting():
    publish_text_only(generate_greeting('evening'))
    print(f"[{datetime.now()}] 🌙 Ночь", flush=True)

# ======================== ПУБЛИКАЦИЯ =========================
def publish_text_only(text):
    for part in split_into_parts(text, max_len=1000):
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML"}, timeout=30)
        if r.status_code != 200: return False
    return True

def publish_to_telegram(text, image_path, session_id=None):
    if not image_path or not os.path.exists(image_path): return False
    with open(image_path, "rb") as photo:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                          files={"photo": photo}, data={"chat_id": TELEGRAM_CHAT_ID}, timeout=30)
        if r.status_code != 200: return False
        if session_id:
            mid = r.json().get('result', {}).get('message_id')
            if mid: execute_query('UPDATE posts SET message_id = ? WHERE session_id = ?', (mid, session_id))
    for part in split_into_parts(text, max_len=1000):
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML"}, timeout=30)
        if r.status_code != 200: return False
    return True

def send_for_approval_no_image(post_text, topic, format_type):
    session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
    save_post(session_id, post_text, "", "", topic, format_type)
    parts = split_into_parts(post_text, max_len=1000)
    total = len(parts)
    for i, part in enumerate(parts, 1):
        caption = f"📝 Пост (без фото, {i}/{total}):\n\n{part}" if total > 1 else f"📝 Пост (без фото):\n\n{part}"
        reply_markup = None
        if i == 1:
            reply_markup = json.dumps({"inline_keyboard": [[
                {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
            ]]})
        td = {"chat_id": ADMIN_CHAT_ID, "text": caption, "parse_mode": "HTML"}
        if reply_markup: td["reply_markup"] = reply_markup
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=td, timeout=30)
    return True

def send_for_approval(post_text, image_path, image_prompt, session_id, topic, format_type):
    save_post(session_id, post_text, image_path, image_prompt, topic, format_type)
    with open(image_path, "rb") as photo:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                          files={"photo": photo}, data={"chat_id": ADMIN_CHAT_ID}, timeout=30)
        if r.status_code != 200: return False
    parts = split_into_parts(post_text, max_len=1000)
    total = len(parts)
    for i, part in enumerate(parts, 1):
        caption = f"📝 Пост ({i}/{total}):\n\n{part}" if total > 1 else f"📝 Пост:\n\n{part}"
        reply_markup = None
        if i == 1:
            reply_markup = json.dumps({"inline_keyboard": [[
                {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
            ]]})
        td = {"chat_id": ADMIN_CHAT_ID, "text": caption, "parse_mode": "HTML"}
        if reply_markup: td["reply_markup"] = reply_markup
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=td, timeout=30)
    return True

def approve_and_publish(session_id):
    post = get_post(session_id)
    if not post: return False
    ok = False
    if post['image_path'] and os.path.exists(post['image_path']):
        ok = publish_to_telegram(post['text'], post['image_path'], session_id)
    if not ok: ok = publish_text_only(post['text'])
    if ok:
        update_post_status(session_id, 'published')
        execute_query('UPDATE posts SET published_at = ? WHERE session_id = ?',
                      (datetime.now().isoformat(), session_id))
    return ok

def send_message(chat_id, text, reply_markup=None):
    if chat_id is None: return
    try:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup: data["reply_markup"] = reply_markup
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=data, timeout=10)
    except Exception as e:
        print(f"[ERROR] send_message: {e}", flush=True)

# ======================== МЕНЮ =========================
def send_admin_menu(chat_id):
    if chat_id is None: return
    text = "🔧 Панель управления:\nВыберите действие:"
    reply_markup = json.dumps({"inline_keyboard": [
        [{"text": "🔄 Сгенерировать пост", "callback_data": "admin_generate"}],
        [{"text": "🔥 Вирусная новость", "callback_data": "admin_viral"}],
        [{"text": "📰 Утренний дайджест", "callback_data": "admin_digest"}],
        [{"text": "🗳 Опрос недели", "callback_data": "admin_poll"}],
        [{"text": "😂 Мем-пятница", "callback_data": "admin_meme"}],
        [{"text": "🤖 Кого убил ИИ", "callback_data": "admin_aikilled"}],
        [{"text": "📊 Статистика", "callback_data": "admin_stats"}],
        [{"text": "🏆 Топ за месяц", "callback_data": "admin_top"}],
        [{"text": "📜 Последние посты", "callback_data": "admin_list"}],
        [{"text": "📝 Показать промпт", "callback_data": "admin_prompt"}],
        [{"text": "✏️ Изменить промпт", "callback_data": "admin_setprompt"}],
        [{"text": "💾 Бэкап", "callback_data": "admin_backup"}]
    ]})
    send_message(chat_id, text, reply_markup=reply_markup)

# ======================== ОБРАБОТЧИКИ =========================
edit_mode = {}
awaiting_prompt = {}

def answer_callback(chat_id, message_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10)
    except: pass

def process_callback(callback_data, chat_id, message_id):
    if callback_data.startswith('admin_'):
        action = callback_data.split('_', 1)[1]
        if action == 'generate':
            answer_callback(chat_id, message_id, "🔄 Запускаю...")
            threading.Thread(target=lambda: job(auto_publish=False), daemon=True).start()
        elif action == 'viral':
            answer_callback(chat_id, message_id, "🔥 Ищу...")
            threading.Thread(target=generate_viral_post, daemon=True).start()
        elif action == 'digest':
            answer_callback(chat_id, message_id, "📰 Собираю...")
            threading.Thread(target=morning_digest, daemon=True).start()
        elif action == 'poll':
            answer_callback(chat_id, message_id, "🗳 Создаю...")
            threading.Thread(target=weekly_poll, daemon=True).start()
        elif action == 'meme':
            answer_callback(chat_id, message_id, "😂 Генерирую...")
            threading.Thread(target=friday_meme, daemon=True).start()
        elif action == 'aikilled':
            answer_callback(chat_id, message_id, "🤖 Генерирую...")
            threading.Thread(target=ai_killed_series, daemon=True).start()
        elif action == 'stats':
            rows = execute_query("SELECT COUNT(*) as total, SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as p, SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as r FROM posts", fetchone=True)
            send_message(chat_id, f"📊 Всего: {rows['total']}\nОпубликовано: {rows['p']}\nОтклонено: {rows['r']}")
            send_admin_menu(chat_id)
        elif action == 'top':
            posts = get_monthly_top(5)
            if not posts: send_message(chat_id, "📭 Нет данных.")
            else:
                msg = "🏆 Топ-5 за месяц:\n\n"
                for i, p in enumerate(posts, 1):
                    short = (p['text'] or "")[:80].replace('\n', ' ')
                    msg += f"{i}. {short}... (👁 {p['views']}, ❤️ {p['reactions']})\n\n"
                send_message(chat_id, msg)
            send_admin_menu(chat_id)
        elif action == 'backup':
            backup_db()
            send_message(chat_id, "✅ Бэкап создан")
            send_admin_menu(chat_id)
        elif action == 'prompt':
            send_message(chat_id, f"📝 Промпт:\n\n{get_prompt()}")
            send_admin_menu(chat_id)
        elif action == 'list':
            posts = get_last_posts(5)
            if not posts: send_message(chat_id, "📭 Нет постов.")
            else:
                msg = "📜 Последние посты:\n\n"
                for p in posts:
                    short = (p['text'] or "")[:80].replace('\n', ' ')
                    msg += f"• [{p['status']}] {short}...\n\n"
                send_message(chat_id, msg)
            send_admin_menu(chat_id)
        elif action == 'setprompt':
            awaiting_prompt[chat_id] = True
            answer_callback(chat_id, message_id, "✏️ Отправьте промпт. /cancel — отмена")
        return

    if any(callback_data.startswith(p) for p in ['approve_', 'regenerate_', 'edit_', 'reject_']):
        action, session_id = callback_data.split('_', 1)
        post_data = get_post(session_id)
        if not post_data:
            answer_callback(chat_id, message_id, "🔄 Черновик устарел"); return
        if post_data["status"] in ("published", "rejected"):
            answer_callback(chat_id, message_id, f"ℹ️ Уже {post_data['status']}"); return
        if action == "approve":
            answer_callback(chat_id, message_id, "🚀 Публикую...")
            if approve_and_publish(session_id):
                answer_callback(chat_id, message_id, "✅ Опубликовано!")
            else:
                answer_callback(chat_id, message_id, "❌ Ошибка публикации")
        elif action == "regenerate":
            answer_callback(chat_id, message_id, "🔄 Генерирую...")
            try:
                r = generate_post()
                if not r or not r[0]:
                    answer_callback(chat_id, message_id, "❌ API не ответил")
                    return
                new_text, new_prompt, new_topic, new_format = r
                new_img = generate_image_strict(new_prompt, max_attempts=3)
                new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
                delete_post(session_id)
                if new_img:
                    send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic, new_format)
                else:
                    send_for_approval_no_image(new_text, new_topic, new_format)
            except Exception as e:
                answer_callback(chat_id, message_id, f"❌ {str(e)[:100]}")
        elif action == "edit":
            answer_callback(chat_id, message_id, "✏️ Пришли новый текст.")
            edit_mode[chat_id] = session_id
        elif action == "reject":
            update_post_status(session_id, 'rejected')
            answer_callback(chat_id, message_id, "❌ Отклонён")
        return
    answer_callback(chat_id, message_id, "Неизвестная команда")

def handle_admin_command(text, chat_id):
    if chat_id is None: return
    if chat_id in awaiting_prompt:
        if text == "/cancel":
            del awaiting_prompt[chat_id]
            send_message(chat_id, "❌ Отменено"); send_admin_menu(chat_id); return
        set_prompt(text)
        del awaiting_prompt[chat_id]
        send_message(chat_id, "✅ Промпт обновлён!"); send_admin_menu(chat_id); return
    if chat_id in edit_mode:
        sid = edit_mode.pop(chat_id)
        execute_query('UPDATE posts SET text = ? WHERE session_id = ?', (text, sid))
        send_message(chat_id, "✅ Текст обновлён"); send_admin_menu(chat_id); return
    if text.startswith('/start') or text.startswith('/help'):
        send_admin_menu(chat_id); return
    if text.startswith('/generate'):
        parts = text.split(' ', 1)
        if len(parts) > 1:
            threading.Thread(target=lambda: job(auto_publish=False, custom_topic=parts[1]), daemon=True).start()
        else:
            threading.Thread(target=lambda: job(auto_publish=False), daemon=True).start()
        send_message(chat_id, "🔄 Запускаю..."); return
    if text.startswith('/viral'):
        send_message(chat_id, "🔥 Ищу..."); threading.Thread(target=generate_viral_post, daemon=True).start(); return
    if text.startswith('/digest'):
        send_message(chat_id, "📰 Собираю..."); threading.Thread(target=morning_digest, daemon=True).start(); return
    if text.startswith('/poll'):
        send_message(chat_id, "🗳 Создаю..."); threading.Thread(target=weekly_poll, daemon=True).start(); return
    if text.startswith('/meme'):
        send_message(chat_id, "😂 Генерирую..."); threading.Thread(target=friday_meme, daemon=True).start(); return
    if text.startswith('/aikilled'):
        send_message(chat_id, "🤖 Генерирую..."); threading.Thread(target=ai_killed_series, daemon=True).start(); return
    if text.startswith('/top'):
        posts = get_monthly_top(5)
        if not posts: send_message(chat_id, "📭 Нет данных.")
        else:
            msg = "🏆 Топ-5 за месяц:\n\n"
            for i, p in enumerate(posts, 1):
                short = (p['text'] or "")[:80].replace('\n', ' ')
                msg += f"{i}. {short}... (👁 {p['views']})\n\n"
            send_message(chat_id, msg)
        send_admin_menu(chat_id); return
    if text.startswith('/stats'):
        rows = execute_query("SELECT COUNT(*) as total, SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as p, SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as r FROM posts", fetchone=True)
        send_message(chat_id, f"📊 Всего: {rows['total']}, опубликовано: {rows['p']}, отклонено: {rows['r']}")
        send_admin_menu(chat_id); return
    if text.startswith('/setprompt'):
        awaiting_prompt[chat_id] = True
        send_message(chat_id, "✏️ Отправьте промпт. /cancel — отмена"); return
    if text == "/cancel":
        awaiting_prompt.pop(chat_id, None)
        send_message(chat_id, "❌ Отменено"); send_admin_menu(chat_id); return
    send_admin_menu(chat_id)

# ======================== БЭКАП =========================
def backup_db():
    if db_type == 'sqlite':
        try:
            os.makedirs("backups", exist_ok=True)
            if os.path.exists(DB_PATH):
                dst = f"backups/posts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                shutil.copyfile(DB_PATH, dst)
                print(f"[INFO] Бэкап: {dst}", flush=True)
        except Exception as e:
            print(f"[ERROR] Бэкап: {e}", flush=True)

# ======================== СТАТИСТИКА =========================
def update_post_stats():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query("SELECT id, session_id, message_id FROM posts WHERE status='published' AND published_at >= ? AND message_id IS NOT NULL", (week_ago,), fetch=True)
    for row in rows:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMessageStatistics",
                             params={"chat_id": TELEGRAM_CHAT_ID, "message_id": row['message_id']}, timeout=10)
            if r.status_code == 200 and r.json().get('ok'):
                stats = r.json().get('result', {})
                views = stats.get('views', 0)
                reactions = sum(x.get('count', 0) for x in stats.get('reactions', []))
                execute_query('UPDATE posts SET views = ?, reactions = ? WHERE id = ?', (views, reactions, row['id']))
            time.sleep(0.5)
        except Exception as e:
            print(f"[ERROR] stats: {e}", flush=True)

def weekly_report():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    stats = execute_query("SELECT COUNT(*) as total, SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as p FROM posts WHERE created_at >= ?", (week_ago,), fetchone=True)
    top = execute_query("SELECT text, views FROM posts WHERE status='published' AND published_at >= ? ORDER BY views DESC LIMIT 3", (week_ago,), fetch=True)
    msg = f"📊 Отчёт за неделю:\n\nВсего: {stats['total']}\nОпубликовано: {stats['p']}\n\n🏆 Топ-3:\n"
    for i, p in enumerate(top or [], 1):
        short = (p['text'] or "")[:100]
        msg += f"{i}. {short}... (👁 {p['views']})\n\n"
    send_message(ADMIN_CHAT_ID, msg)

def digest_job():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query("SELECT text, views FROM posts WHERE status='published' AND published_at >= ? ORDER BY views DESC LIMIT 5", (week_ago,), fetch=True)
    if not rows:
        send_message(ADMIN_CHAT_ID, "📊 Нет постов за неделю."); return
    digest = "📅 Лучшие посты недели:\n\n"
    for i, row in enumerate(rows, 1):
        short = (row['text'] or "")[:100]
        digest += f"{i}. {short}... (👁 {row['views']})\n\n"
    send_message(ADMIN_CHAT_ID, digest)

def job(auto_publish=False, custom_topic=None):
    print(f"[DEBUG] job started", flush=True)
    send_message(ADMIN_CHAT_ID, f"🔄 Генерация начата в {datetime.now().strftime('%H:%M:%S')}")
    try:
        r = generate_post(custom_topic=custom_topic)
        if not r or not r[0]:
            send_message(ADMIN_CHAT_ID, "❌ API не ответил после 3 попыток")
            return
        post_text, image_prompt, topic, format_type = r
        image_path = generate_image_strict(image_prompt, max_attempts=3)
        if not image_path:
            if auto_publish: publish_text_only(post_text)
            else: send_for_approval_no_image(post_text, topic, format_type)
            return
        if auto_publish:
            publish_to_telegram(post_text, image_path)
            send_message(ADMIN_CHAT_ID, "✅ Опубликовано (авто)")
        else:
            session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
            send_for_approval(post_text, image_path, image_prompt, session_id, topic, format_type)
            send_message(ADMIN_CHAT_ID, "✅ Пост на модерации")
    except Exception as e:
        print(f"[ERROR] job: {e}", flush=True)
        traceback.print_exc()
        send_message(ADMIN_CHAT_ID, f"❌ Ошибка: {str(e)[:100]}")

# ======================== ПОЛЛИНГ =========================
def poll_updates():
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 30, "allowed_updates": ["callback_query", "message"]},
                             timeout=35)
            if r.status_code != 200: time.sleep(5); continue
            data = r.json()
            if not data.get("ok"): time.sleep(5); continue
            for update in data.get("result", []):
                try:
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        if cb.get("data"):
                            process_callback(cb["data"], cb["message"]["chat"]["id"], cb["id"])
                            try:
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                              json={"callback_query_id": cb["id"], "text": "Ок"}, timeout=10)
                            except: pass
                    elif "message" in update:
                        mc = update["message"].get("chat", {}).get("id")
                        if ADMIN_CHAT_ID and mc == int(ADMIN_CHAT_ID):
                            txt = update["message"].get("text", "")
                            if txt: handle_admin_command(txt, mc)
                    offset = update["update_id"] + 1
                except Exception as inner:
                    print(f"[ERROR] update: {inner}", flush=True)
                    offset = update["update_id"] + 1
        except Exception as e:
            print(f"[ERROR] poll: {e}", flush=True)
            time.sleep(5)

# ======================== ВЕБ-СЕРВЕР =========================
def run_job_async():
    try: job(auto_publish=False)
    except Exception as e: print(f"[ERROR] {e}", flush=True)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            threading.Thread(target=run_job_async, daemon=True).start()
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        elif self.path == '/digest':
            threading.Thread(target=morning_digest, daemon=True).start()
            self.send_response(200); self.end_headers(); self.wfile.write(b"Digest started")
        elif self.path == '/poll':
            threading.Thread(target=weekly_poll, daemon=True).start()
            self.send_response(200); self.end_headers(); self.wfile.write(b"Poll started")
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()
    def log_message(self, *args): pass

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=start_health_server, daemon=True).start()

def keep_alive():
    while True:
        try: urllib.request.urlopen("https://skeptik-bot.onrender.com", timeout=10)
        except: pass
        time.sleep(600)

threading.Thread(target=keep_alive, daemon=True).start()
threading.Thread(target=poll_updates, daemon=True).start()

# ======================== РАСПИСАНИЕ =========================
# 6 генераций для МОДЕРАЦИИ (UTC = МСК - 3)
schedule.every().day.at("07:00").do(lambda: job(auto_publish=False))   # 10:00 МСК
schedule.every().day.at("10:00").do(lambda: job(auto_publish=False))   # 13:00 МСК
schedule.every().day.at("13:00").do(lambda: job(auto_publish=False))   # 16:00 МСК
schedule.every().day.at("15:30").do(lambda: job(auto_publish=False))   # 18:30 МСК
schedule.every().day.at("18:00").do(lambda: job(auto_publish=False))   # 21:00 МСК
schedule.every().day.at("18:30").do(lambda: job(auto_publish=False))   # 21:30 МСК

# ☀️ УТРО: сначала приветствие, потом дайджест
schedule.every().day.at("04:30").do(send_morning_greeting)   # 07:30 МСК
schedule.every().day.at("05:00").do(morning_digest)          # 08:00 МСК

# 🌙 ВЕЧЕР: сначала последний пост, потом спокойной ночи
schedule.every().day.at("19:30").do(send_evening_greeting)   # 22:30 МСК

# Срочные проверки сенсаций
schedule.every().day.at("02:00").do(check_urgent_viral)      # 05:00 МСК
schedule.every().day.at("08:00").do(check_urgent_viral)      # 11:00 МСК
schedule.every().day.at("14:00").do(check_urgent_viral)      # 17:00 МСК
schedule.every().day.at("20:00").do(check_urgent_viral)      # 23:00 МСК

# Вирусная новость недели (вт и пт)
schedule.every().tuesday.at("09:00").do(generate_viral_post)   # 12:00 МСК
schedule.every().friday.at("09:00").do(generate_viral_post)    # 12:00 МСК

# Опрос «Абсурд недели» (вс)
schedule.every().sunday.at("16:00").do(weekly_poll)          # 19:00 МСК

# Мем-пятница
schedule.every().friday.at("13:00").do(friday_meme)          # 16:00 МСК

# Серия «Кого убил ИИ» (ср)
schedule.every().wednesday.at("11:00").do(ai_killed_series)  # 14:00 МСК

# Аналитика и служебное
schedule.every().sunday.at("17:00").do(weekly_report)
schedule.every().sunday.at("17:00").do(digest_job)
schedule.every().day.at("03:00").do(backup_db)
schedule.every().day.at("01:00").do(update_post_stats)

print("=" * 50, flush=True)
print("🚀 Бот запущен!", flush=True)
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}", flush=True)
print("Генерация: 10:00, 13:00, 16:00, 18:30, 21:00, 21:30 МСК", flush=True)
print("☀️ 07:30 приветствие → 08:00 дайджест (без 'Доброе утро')", flush=True)
print("🌙 22:30 спокойной ночи", flush=True)
print("🔥 Вирусные: вт/пт 12:00 | 😂 Мем: пт 16:00 | 🤖 ИИ: ср 14:00 | 🗳 Опрос: вс 19:00", flush=True)
print(f"Unsplash: {'подключён' if UNSPLASH_ACCESS_KEY else 'не подключён'}", flush=True)
print("=" * 50, flush=True)

while True:
    schedule.run_pending()
    time.sleep(60)