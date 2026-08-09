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
import shutil
import sqlite3
from contextlib import closing

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")  # опционально

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
API_PROVIDER = "openai"

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

POST_FORMATS = {
    0: "мем",
    1: "новость",
    2: "аналитика",
    3: "мем",
    4: "новость",
    5: "аналитика",
    6: "мем"
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

# ======================== БАЗА ДАННЫХ =========================
if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    def get_db_connection():
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    db_type = 'postgres'
else:
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
                reactions INTEGER DEFAULT 0,
                format TEXT DEFAULT 'новость'
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_publish ON posts(scheduled_publish_time)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posts(topic)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS prompts (
                name TEXT PRIMARY KEY,
                content TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS publish_times (
                id SERIAL PRIMARY KEY,
                post_id INTEGER,
                publish_hour INTEGER,
                publish_weekday INTEGER,
                views INTEGER,
                reactions INTEGER
            )
        ''')
        default_prompt = (
            "Ты — автор канала «Скептик с EBITDA».\n"
            "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
            "ОБЯЗАТЕЛЬНО используй эмодзи в каждом абзаце (минимум 3–4 разных).\n"
            "Начинай пост с заголовка с эмодзи, а каждый смысловой блок – с нового эмодзи.\n"
            "Добавляй ёмкие, эмоциональные комментарии к цифрам, чтобы текст был живым и запоминающимся.\n"
            "Структура: заголовок → факты с сарказмом → вывод → Action Item с ✅.\n"
            "Не используй шаблонные фразы – будь оригинальным.\n"
            "Используй ТОЛЬКО данные за 2025–2026 годы.\n"
            "Пост должен быть примерно 600–900 символов (6–8 предложений). ОБЯЗАТЕЛЬНО заканчивай точкой, восклицанием или вопросом.\n"
            "Ключевые цифры выделяй жирным через HTML-тег <b>...</b> (НЕ используй **).\n"
            "После Action Item — источник (если неизвестен, укажи 'по данным открытых источников') и хештеги (#тег1 #тег2).\n"
            "Не используй разделители вроде '---'.\n"
            "После текста === и описание картинки (англ., 3–4 слова)."
        )
        cur.execute('INSERT INTO prompts (name, content) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING', ('system_prompt', default_prompt))
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
                    reactions INTEGER DEFAULT 0,
                    format TEXT DEFAULT 'новость'
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON posts(session_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON posts(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_publish ON posts(scheduled_publish_time)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posts(topic)')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS prompts (
                    name TEXT PRIMARY KEY,
                    content TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS publish_times (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER,
                    publish_hour INTEGER,
                    publish_weekday INTEGER,
                    views INTEGER,
                    reactions INTEGER,
                    FOREIGN KEY(post_id) REFERENCES posts(id)
                )
            ''')
            default_prompt_sqlite = (
                "Ты — автор канала «Скептик с EBITDA».\n"
                "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
                "ОБЯЗАТЕЛЬНО используй эмодзи в каждом абзаце (минимум 3–4 разных).\n"
                "Начинай пост с заголовка с эмодзи, а каждый смысловой блок – с нового эмодзи.\n"
                "Добавляй ёмкие, эмоциональные комментарии к цифрам, чтобы текст был живым и запоминающимся.\n"
                "Структура: заголовок → факты с сарказмом → вывод → Action Item с ✅.\n"
                "Не используй шаблонные фразы – будь оригинальным.\n"
                "Используй ТОЛЬКО данные за 2025–2026 годы.\n"
                "Пост должен быть примерно 600–900 символов (6–8 предложений). ОБЯЗАТЕЛЬНО заканчивай точкой, восклицанием или вопросом.\n"
                "Ключевые цифры выделяй жирным через HTML-тег <b>...</b> (НЕ используй **).\n"
                "После Action Item — источник (если неизвестен, укажи 'по данным открытых источников') и хештеги (#тег1 #тег2).\n"
                "Не используй разделители вроде '---'.\n"
                "После текста === и описание картинки (англ., 3–4 слова)."
            )
            conn.execute('INSERT OR IGNORE INTO prompts (name, content) VALUES (?, ?)', ('system_prompt', default_prompt_sqlite))
            conn.commit()
init_db()

def execute_query(query, params=None, fetch=False, fetchone=False):
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor if fetch or fetchone else None)
        cur.execute(query, params or ())
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
            cur.execute(query, params or ())
            if fetch:
                result = [dict(row) for row in cur.fetchall()]
            elif fetchone:
                row = cur.fetchone()
                result = dict(row) if row else None
            else:
                result = None
            conn.commit()
            return result

def get_prompt():
    row = execute_query('SELECT content FROM prompts WHERE name = ?', ('system_prompt',), fetchone=True)
    return row['content'] if row else None

def set_prompt(content):
    if db_type == 'postgres':
        execute_query('INSERT INTO prompts (name, content) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET content = EXCLUDED.content', ('system_prompt', content))
    else:
        execute_query('REPLACE INTO prompts (name, content) VALUES (?, ?)', ('system_prompt', content))

# ======================== ПОЛУЧЕНИЕ ТЕМЫ =========================
def get_topic_from_news():
    rss_urls = [
        "https://www.rbc.ru/rss/",
        "https://www.kommersant.ru/RSS/news.xml",
        "https://lenta.ru/rss/news",
        "https://www.vedomosti.ru/rss/news",
        "https://www.vedomosti.ru/rss/finance"
    ]
    keywords = ["ozon", "wildberries", "магнит", "ритейл", "торговля", "нефть", "лукойл", "маркетплейс", "выручка", "прибыль"]
    try:
        for url in rss_urls:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = entry.title.lower()
                summary = entry.summary.lower() if hasattr(entry, 'summary') else ""
                if any(kw in title or kw in summary for kw in keywords):
                    published = entry.get('published', '')
                    if published:
                        try:
                            pub_date = datetime.strptime(published[:25], '%a, %d %b %Y %H:%M:%S %Z') if 'GMT' in published else None
                            if pub_date and pub_date < datetime.now() - timedelta(days=30):
                                continue
                        except:
                            pass
                    return f"{entry.title}. {summary[:150]}"
        return DAY_TOPICS.get(datetime.now().weekday(), DAY_TOPICS[0])
    except Exception as e:
        print(f"[WARN] Ошибка RSS: {e}")
        return DAY_TOPICS.get(datetime.now().weekday(), DAY_TOPICS[0])

def get_topic_by_analytics():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT topic, rating, views, reactions FROM posts WHERE status = \'published\' AND published_at >= ? AND topic IS NOT NULL AND topic != \'\'',
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
        topic_stats[topic] = topic_stats.get(topic, 0) + score

    if not topic_stats:
        return get_topic_from_news()

    best_topic = max(topic_stats, key=topic_stats.get)
    print(f"[DEBUG] Лучшая тема по аналитике: {best_topic} (score: {topic_stats[best_topic]:.1f})")
    return best_topic

# ======================== БЭКАП =========================
def backup_db():
    try:
        if not os.path.exists("backups"):
            os.makedirs("backups")
        if db_type == 'sqlite':
            src = DB_PATH
            if os.path.exists(src):
                dst = f"backups/posts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                shutil.copyfile(src, dst)
                print(f"[INFO] Бэкап создан: {dst}")
        else:
            print("[INFO] Бэкап PostgreSQL не реализован, используйте встроенные средства Render")
    except Exception as e:
        print(f"[ERROR] Ошибка бэкапа: {e}")

# ======================== ФУНКЦИИ ПОСТОВ =========================
def save_post(session_id, text, image_path, image_prompt, topic, format_type):
    if db_type == 'postgres':
        query = '''
            INSERT INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at, format)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                text = EXCLUDED.text,
                image_path = EXCLUDED.image_path,
                image_prompt = EXCLUDED.image_prompt,
                topic = EXCLUDED.topic,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at,
                format = EXCLUDED.format
        '''
        params = (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat(), format_type)
    else:
        query = 'INSERT OR REPLACE INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at, format) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
        params = (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat(), format_type)
    execute_query(query, params)
    print(f"[DEBUG] Пост сохранён: {session_id} (формат: {format_type})")

def get_post(session_id):
    row = execute_query('SELECT text, image_path, image_prompt, status, scheduled_publish_time, edit_pending, rating, reposted, message_id, topic, format FROM posts WHERE session_id = ?', (session_id,), fetchone=True)
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

def get_last_posts(limit=5):
    rows = execute_query(
        'SELECT topic, status, created_at, text, format FROM posts ORDER BY created_at DESC LIMIT ?',
        (limit,), fetch=True
    )
    return rows

# ======================== ОБРАБОТКА ТЕКСТА =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

def beautify_post(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\*\*', '', text)  # убираем Markdown звёздочки

    action_text = ""
    match = re.search(r'(✅.*?)(?=\s*[A-Z#]|$)', text, re.DOTALL)
    if not match:
        match = re.search(r'(Action Item:.*?)(?=\s*[A-Z#]|$)', text, re.DOTALL)
    if match:
        action_text = match.group(1).strip()
        text = text.replace(action_text, '').strip()
        if not action_text.startswith('✅'):
            action_text = '✅ ' + action_text

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    paragraphs = []
    i = 0
    while i < len(sentences):
        if i + 1 < len(sentences):
            paragraphs.append(sentences[i] + ' ' + sentences[i + 1])
            i += 2
        else:
            paragraphs.append(sentences[i])
            i += 1
    text = '\n\n'.join(paragraphs)

    def replacer(m):
        num = m.group(0)
        if not re.search(r'<b>.*?' + re.escape(num) + r'.*?</b>', text):
            return f'<b>{num}</b>'
        return num
    text = re.sub(r'\b(\d+[.,]?\d*)\b', replacer, text)

    if action_text:
        hashtag_match = re.search(r'(#\w+(?:\s*#\w+)*)$', text)
        if hashtag_match:
            hashtags = hashtag_match.group(1)
            text = text.replace(hashtags, '').strip()
            text = text + f'\n\n<b>{action_text}</b>\n\n{hashtags}'
        else:
            text = text + f'\n\n<b>{action_text}</b>'

    text = re.sub(r'\n{3,}', '\n\n', text)
    return text

def split_into_parts(text, max_len=1000):
    if len(text) <= max_len:
        return [text]
    paragraphs = text.split('\n\n')
    result_parts = []
    current_part = ""
    for para in paragraphs:
        if not para.strip():
            continue
        if len(current_part) + len(para) + 2 <= max_len:
            if current_part:
                current_part += '\n\n' + para
            else:
                current_part = para
        else:
            if len(para) > max_len:
                sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
                for sent in sentences:
                    if len(current_part) + len(sent) + 2 <= max_len:
                        if current_part:
                            current_part += ' ' + sent
                        else:
                            current_part = sent
                    else:
                        if current_part:
                            result_parts.append(current_part)
                        current_part = sent
            else:
                if current_part:
                    result_parts.append(current_part)
                current_part = para
    if current_part:
        result_parts.append(current_part)
    return result_parts if result_parts else [text[:max_len] + "..."]

# ======================== ПОИСК КАРТИНКИ НА UNSPLASH =========================
def search_image_unsplash(query):
    if not UNSPLASH_ACCESS_KEY:
        print("[WARN] UNSPLASH_ACCESS_KEY не задан, возвращаем None")
        return None
    try:
        url = "https://api.unsplash.com/search/photos"
        params = {
            "query": query,
            "per_page": 1,
            "orientation": "landscape"
        }
        headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results") and len(data["results"]) > 0:
                image_url = data["results"][0]["urls"]["regular"]
                img_resp = requests.get(image_url, timeout=30)
                if img_resp.status_code == 200:
                    with open("temp_image.jpg", "wb") as f:
                        f.write(img_resp.content)
                    try:
                        img = Image.open("temp_image.jpg")
                        if img.width < 200 or img.height < 200:
                            print("[WARN] Слишком маленькое изображение, пробуем следующее")
                            os.remove("temp_image.jpg")
                            return None
                    except:
                        pass
                    return "temp_image.jpg"
        print(f"[WARN] Unsplash вернул статус {resp.status_code}")
        return None
    except Exception as e:
        print(f"[ERROR] Ошибка при запросе к Unsplash: {e}")
        return None

# ======================== ГЕНЕРАЦИЯ ПОСТА =========================
def generate_post(custom_topic=None):
    if custom_topic:
        topic = custom_topic
        print(f"[DEBUG] Использую ручную тему: {topic}")
    else:
        topic = get_topic_by_analytics()
        print(f"[DEBUG] Выбрана тема по аналитике: {topic}")

    format_type = POST_FORMATS.get(datetime.now().weekday(), "новость")
    print(f"[DEBUG] Формат: {format_type}")

    system_prompt = get_prompt()
    if not system_prompt:
        system_prompt = (
            "Ты — автор канала «Скептик с EBITDA».\n"
            "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
            "ОБЯЗАТЕЛЬНО используй эмодзи в каждом абзаце (минимум 3–4 разных).\n"
            "Начинай пост с заголовка с эмодзи, а каждый смысловой блок – с нового эмодзи.\n"
            "Добавляй ёмкие, эмоциональные комментарии к цифрам, чтобы текст был живым и запоминающимся.\n"
            "Структура: заголовок → факты с сарказмом → вывод → Action Item с ✅.\n"
            "Не используй шаблонные фразы – будь оригинальным.\n"
            "Используй ТОЛЬКО данные за 2025–2026 годы.\n"
            "Пост должен быть примерно 600–900 символов (6–8 предложений). ОБЯЗАТЕЛЬНО заканчивай точкой, восклицанием или вопросом.\n"
            "Ключевые цифры выделяй жирным через HTML-тег <b>...</b> (НЕ используй **).\n"
            "После Action Item — источник (если неизвестен, укажи 'по данным открытых источников') и хештеги (#тег1 #тег2).\n"
            "Не используй разделители вроде '---'.\n"
            "После текста === и описание картинки (англ., 3–4 слова)."
        )

    format_style = {
        "мем": "Сделай пост с юмором, сарказмом, коротко (до 500 символов).",
        "новость": "Информативный пост с фактами и датами (до 500 символов).",
        "аналитика": "Глубокий разбор цифр и трендов, но кратко (до 500 символов)."
    }.get(format_type, "")

    user_prompt = f"Напиши пост на тему: {topic}. {format_style} Используй реальные цифры из отчётов (только 2025–2026 годов)."

    headers = API_HEADERS_FUNC(DEEPSEEK_API_KEY)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.85,
        "max_tokens": 1000
    }

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=90)
        if response.status_code != 200:
            raise Exception(f"API вернул {response.status_code}: {response.text}")
        data = response.json()
        if "choices" not in data or not data["choices"]:
            raise Exception("Нет choices")
        full_text = data["choices"][0]["message"]["content"]
        if not full_text:
            raise Exception("Пустой ответ")
    except Exception as e:
        print(f"[ERROR] Ошибка генерации: {e}")
        full_text = "📊 Скептик с EBITDA: аналитика ритейла.\n\n⚠️ К сожалению, API временно недоступен. Попробуйте позже.\n\n✅ Следите за обновлениями!"

    full_text = clean_text(full_text)
    if full_text and full_text[-1] not in ('.', '!', '?'):
        full_text += '.'

    if "===" in full_text:
        parts = full_text.split("===", 1)
        post_text = parts[0].strip()
        image_prompt = parts[1].strip() if len(parts) > 1 else ""
    else:
        post_text = full_text.strip()
        image_prompt = ""

    if len(image_prompt) < 10:
        image_prompt = f"modern business illustration, {topic}, financial data, charts, sarcastic, colorful, infographic style"
    else:
        if "illustration" not in image_prompt.lower():
            image_prompt += ", modern business illustration, infographic, colorful"

    post_text = beautify_post(post_text)
    return post_text, image_prompt, topic, format_type

# ======================== ГЕНЕРАЦИЯ КАРТИНКИ =========================
def generate_image(prompt):
    if UNSPLASH_ACCESS_KEY:
        keywords = re.sub(r'[^\w\s]', '', prompt)
        keywords = keywords[:100]
        img_path = search_image_unsplash(keywords)
        if img_path:
            return img_path

    print("[WARN] Использую резервную генерацию через Pollinations")
    if len(prompt) > 200:
        prompt = prompt[:200]
    for attempt in range(3):
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
                try:
                    img = Image.open("temp_image.jpg")
                    if img.width < 50 or img.height < 50:
                        raise Exception("Слишком маленькое")
                    img = img.convert('L')
                    pixels = list(img.getdata())
                    avg = sum(pixels) / len(pixels)
                    if avg < 30:
                        print("[WARN] Обнаружено чёрное изображение, пробуем с упрощённым промптом")
                        os.remove("temp_image.jpg")
                        prompt = "business illustration, financial data, modern, colorful"
                        continue
                except:
                    pass
                return "temp_image.jpg"
            else:
                print(f"[WARN] Pollinations вернул {resp.status_code}, попытка {attempt+1}")
        except Exception as e:
            print(f"[WARN] Ошибка Pollinations (попытка {attempt+1}): {e}")
        time.sleep(3)
    try:
        print("[DEBUG] Последняя попытка с минимальным промптом")
        url = f"https://image.pollinations.ai/prompt/business%20illustration%20finance%20chart?width=1200&height=800&seed={random.randint(1,999999)}&t={int(time.time())}"
        resp = requests.get(url, timeout=90)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open("temp_image.jpg", "wb") as f:
                f.write(resp.content)
            return "temp_image.jpg"
    except:
        pass
    print("[ERROR] Все попытки генерации картинки провалились")
    return None

# ======================== ПУБЛИКАЦИЯ =========================
def publish_text_only(text):
    parts = split_into_parts(text, max_len=1000)
    for part in parts:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML"}
        resp = requests.post(url, json=data, timeout=30)
        if resp.status_code != 200:
            return False
    return True

def send_for_approval_no_image(post_text, topic, format_type):
    session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
    save_post(session_id, post_text, "", "", topic, format_type)
    parts = split_into_parts(post_text, max_len=1000)
    total = len(parts)
    for i, part in enumerate(parts, 1):
        if total == 1:
            caption = f"📝 Новый пост на проверку (без картинки):\n\n{part}"
        else:
            caption = f"📝 Новый пост на проверку (без картинки, часть {i}/{total}):\n\n{part}"
        reply_markup = None
        if i == 1:
            reply_markup = json.dumps({
                "inline_keyboard": [
                    [
                        {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                        {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                        {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                        {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                    ]
                ]
            })
        text_data = {"chat_id": ADMIN_CHAT_ID, "text": caption, "parse_mode": "HTML"}
        if reply_markup:
            text_data["reply_markup"] = reply_markup
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка отправки текста (часть {i}): {resp.text}")
            return False
    return True

def publish_to_telegram(text, image_path, session_id=None):
    if not os.path.exists(image_path):
        return False
    with open(image_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": TELEGRAM_CHAT_ID}
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", files=files, data=data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка отправки фото: {resp.text}")
            return False
        if session_id:
            msg_data = resp.json()
            message_id = msg_data.get('result', {}).get('message_id')
            if message_id:
                execute_query('UPDATE posts SET message_id = ? WHERE session_id = ?', (message_id, session_id))
    parts = split_into_parts(text, max_len=1000)
    for part in parts:
        text_data = {"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML"}
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка отправки текста: {resp.text}")
            return False
    return True

def send_for_approval(post_text, image_path, image_prompt, session_id, topic, format_type):
    save_post(session_id, post_text, image_path, image_prompt, topic, format_type)
    with open(image_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": ADMIN_CHAT_ID}
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", files=files, data=data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка отправки фото на модерацию: {resp.text}")
            return False
    parts = split_into_parts(post_text, max_len=1000)
    total = len(parts)
    for i, part in enumerate(parts, 1):
        if total == 1:
            caption = f"📝 Новый пост на проверку:\n\n{part}"
        else:
            caption = f"📝 Новый пост на проверку (часть {i}/{total}):\n\n{part}"
        reply_markup = None
        if i == 1:
            reply_markup = json.dumps({
                "inline_keyboard": [
                    [
                        {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                        {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                        {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                        {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                    ]
                ]
            })
        text_data = {"chat_id": ADMIN_CHAT_ID, "text": caption, "parse_mode": "HTML"}
        if reply_markup:
            text_data["reply_markup"] = reply_markup
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка отправки текста (часть {i}): {resp.text}")
            return False
    return True

def schedule_publish(session_id):
    now = datetime.now(MOSCOW_TZ)
    publish_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= publish_time:
        publish_time += timedelta(days=1)
    update_post_status(session_id, 'approved', scheduled_time=publish_time)
    send_message(ADMIN_CHAT_ID, f"✅ Пост одобрен и запланирован на {publish_time.strftime('%d.%m.%Y %H:%M')} МСК.")

def send_message(chat_id, text, reply_markup=None):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            data["reply_markup"] = reply_markup
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"[ERROR] send_message: {e}")

# ======================== МЕНЮ АДМИНА =========================
def send_admin_menu(chat_id):
    text = "🔧 Панель управления ботом:\nВыберите действие:"
    reply_markup = json.dumps({
        "inline_keyboard": [
            [{"text": "🔄 Сгенерировать пост", "callback_data": "admin_generate"}],
            [{"text": "📊 Статистика", "callback_data": "admin_stats"}],
            [{"text": "💾 Бэкап", "callback_data": "admin_backup"}],
            [{"text": "📝 Показать промпт", "callback_data": "admin_prompt"}],
            [{"text": "🚀 Опубликовать сейчас", "callback_data": "admin_publishnow"}],
            [{"text": "📜 Последние посты", "callback_data": "admin_list"}],
            [{"text": "✏️ Изменить промпт", "callback_data": "admin_setprompt"}]
        ]
    })
    send_message(chat_id, text, reply_markup=reply_markup)

# ======================== ОБРАБОТЧИК КНОПОК И КОМАНД =========================
edit_mode = {}
awaiting_prompt = {}

def process_callback(callback_data, chat_id, message_id):
    if callback_data.startswith('admin_'):
        action = callback_data.split('_', 1)[1]
        if action == 'generate':
            answer_callback(chat_id, message_id, "🔄 Запускаю генерацию...")
            threading.Thread(target=lambda: job(auto_publish=False), daemon=True).start()
            return
        elif action == 'stats':
            rows = execute_query(
                'SELECT COUNT(*) as total, SUM(CASE WHEN status=\'published\' THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status=\'rejected\' THEN 1 ELSE 0 END) as rejected FROM posts',
                fetchone=True
            )
            msg = f"📊 Статистика:\nВсего постов: {rows['total']}\nОпубликовано: {rows['published']}\nОтклонено: {rows['rejected']}"
            answer_callback(chat_id, message_id, msg)
            send_admin_menu(chat_id)
            return
        elif action == 'backup':
            backup_db()
            answer_callback(chat_id, message_id, "✅ Бэкап создан")
            send_admin_menu(chat_id)
            return
        elif action == 'prompt':
            current = get_prompt()
            if current:
                send_message(chat_id, f"📝 Текущий промпт:\n\n{current}")
            else:
                send_message(chat_id, "❌ Промпт не найден")
            answer_callback(chat_id, message_id, "Промпт показан выше")
            send_admin_menu(chat_id)
            return
        elif action == 'publishnow':
            answer_callback(chat_id, message_id, "🚀 Публикую все одобренные посты...")
            threading.Thread(target=publish_scheduled_posts, daemon=True).start()
            return
        elif action == 'list':
            posts = get_last_posts(limit=5)
            if not posts:
                send_message(chat_id, "📭 Нет постов.")
            else:
                msg = "📜 Последние 5 постов:\n\n"
                for p in posts:
                    created = p['created_at'][:16] if p['created_at'] else "??"
                    status = p['status']
                    topic = p['topic'] or "Без темы"
                    short_text = (p['text'] or "")[:100].replace('\n', ' ').strip()
                    fmt = p.get('format', 'новость')
                    msg += f"• {created} [{status}] {fmt} – {topic}\n   {short_text}...\n\n"
                send_message(chat_id, msg)
            answer_callback(chat_id, message_id, "Список показан выше")
            send_admin_menu(chat_id)
            return
        elif action == 'setprompt':
            awaiting_prompt[chat_id] = True
            answer_callback(chat_id, message_id, "✏️ Отправьте новый текст системного промпта (можно многострочный). Для отмены отправьте /cancel")
            return

    # Модерация
    if callback_data.startswith('approve_') or callback_data.startswith('regenerate_') or callback_data.startswith('edit_') or callback_data.startswith('reject_'):
        action, session_id = callback_data.split('_', 1)
        post_data = get_post(session_id)
        if not post_data:
            answer_callback(chat_id, message_id, "🔄 Черновик устарел")
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
                new_text, new_prompt, new_topic, new_format = generate_post()
                new_img = generate_image(new_prompt)
                if not new_img:
                    send_for_approval_no_image(new_text, new_topic, new_format)
                    delete_post(session_id)
                    answer_callback(chat_id, message_id, "🔄 Новый пост отправлен (без картинки)")
                    return
                new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
                delete_post(session_id)
                send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic, new_format)
                answer_callback(chat_id, message_id, "🔄 Новый пост отправлен.")
            except Exception as e:
                answer_callback(chat_id, message_id, f"❌ Ошибка: {str(e)[:100]}")
        elif action == "edit":
            answer_callback(chat_id, message_id, "✏️ Пришли новый текст поста (без картинки).")
            edit_mode[chat_id] = session_id
        elif action == "reject":
            update_post_status(session_id, 'rejected')
            answer_callback(chat_id, message_id, "❌ Пост отклонён.")
        return

    answer_callback(chat_id, message_id, "Неизвестная команда")

def answer_callback(chat_id, message_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

def handle_admin_command(text, chat_id):
    if chat_id in awaiting_prompt:
        if text == "/cancel":
            del awaiting_prompt[chat_id]
            send_message(chat_id, "❌ Отменено.")
            send_admin_menu(chat_id)
            return
        new_prompt = text
        set_prompt(new_prompt)
        del awaiting_prompt[chat_id]
        send_message(chat_id, "✅ Промпт обновлён!")
        send_admin_menu(chat_id)
        return

    if text.startswith('/start') or text.startswith('/help'):
        send_admin_menu(chat_id)
        return

    if text.startswith('/generate'):
        parts = text.split(' ', 1)
        if len(parts) > 1:
            custom_topic = parts[1]
            send_message(chat_id, f"🔄 Генерирую пост на тему: {custom_topic}...")
            threading.Thread(target=lambda: job(auto_publish=False, custom_topic=custom_topic), daemon=True).start()
            send_admin_menu(chat_id)
            return
        else:
            send_message(chat_id, "🔄 Запускаю генерацию...")
            threading.Thread(target=lambda: job(auto_publish=False), daemon=True).start()
            send_admin_menu(chat_id)
            return

    if text.startswith('/stats'):
        rows = execute_query(
            'SELECT COUNT(*) as total, SUM(CASE WHEN status=\'published\' THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status=\'rejected\' THEN 1 ELSE 0 END) as rejected FROM posts',
            fetchone=True
        )
        msg = f"📊 Статистика:\nВсего постов: {rows['total']}\nОпубликовано: {rows['published']}\nОтклонено: {rows['rejected']}"
        send_message(chat_id, msg)
        send_admin_menu(chat_id)
        return

    if text.startswith('/backup'):
        backup_db()
        send_message(chat_id, "✅ Бэкап создан")
        send_admin_menu(chat_id)
        return

    if text.startswith('/prompt'):
        current = get_prompt()
        if current:
            send_message(chat_id, f"📝 Текущий промпт:\n\n{current}")
        else:
            send_message(chat_id, "❌ Промпт не найден")
        send_admin_menu(chat_id)
        return

    if text.startswith('/publishnow'):
        send_message(chat_id, "🚀 Публикую все одобренные посты...")
        threading.Thread(target=publish_scheduled_posts, daemon=True).start()
        send_admin_menu(chat_id)
        return

    if text.startswith('/list'):
        posts = get_last_posts(limit=5)
        if not posts:
            send_message(chat_id, "📭 Нет постов.")
        else:
            msg = "📜 Последние 5 постов:\n\n"
            for p in posts:
                created = p['created_at'][:16] if p['created_at'] else "??"
                status = p['status']
                topic = p['topic'] or "Без темы"
                short_text = (p['text'] or "")[:100].replace('\n', ' ').strip()
                fmt = p.get('format', 'новость')
                msg += f"• {created} [{status}] {fmt} – {topic}\n   {short_text}...\n\n"
            send_message(chat_id, msg)
        send_admin_menu(chat_id)
        return

    if text.startswith('/setprompt'):
        awaiting_prompt[chat_id] = True
        send_message(chat_id, "✏️ Отправьте новый текст системного промпта (можно многострочный). Для отмены отправьте /cancel")
        return

    if text == "/cancel":
        if chat_id in awaiting_prompt:
            del awaiting_prompt[chat_id]
        send_message(chat_id, "❌ Отменено.")
        send_admin_menu(chat_id)
        return

    send_admin_menu(chat_id)

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
                    text = update["message"].get("text", "")
                    if text:
                        handle_admin_command(text, chat_id)
        except Exception as e:
            print(f"[ERROR] poll_updates: {e}")
            time.sleep(5)

# ======================== ОСТАЛЬНЫЙ КОД =========================
def check_and_repost():
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    rows = execute_query(
        'SELECT session_id, text FROM posts WHERE status = \'published\' AND reposted = FALSE AND rating >= 3 AND published_at <= ?',
        (cutoff,), fetch=True
    )
    for row in rows:
        if publish_text_only(row['text']):
            execute_query('UPDATE posts SET reposted = TRUE WHERE session_id = ?', (row['session_id'],))
            print(f"[DEBUG] Повторно опубликован пост {row['session_id']}")

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

def record_publish_time(post_id, views, reactions):
    if db_type == 'postgres':
        execute_query(
            'INSERT INTO publish_times (post_id, publish_hour, publish_weekday, views, reactions) '
            'SELECT id, EXTRACT(HOUR FROM created_at)::int, EXTRACT(DOW FROM created_at)::int, %s, %s FROM posts WHERE id = %s',
            (views, reactions, post_id)
        )
    else:
        execute_query(
            'INSERT INTO publish_times (post_id, publish_hour, publish_weekday, views, reactions) '
            'SELECT id, strftime("%H", created_at), strftime("%w", created_at), ?, ? FROM posts WHERE id = ?',
            (views, reactions, post_id)
        )

def analyze_best_time():
    rows = execute_query(
        'SELECT publish_hour, AVG(views) as avg_views FROM publish_times GROUP BY publish_hour ORDER BY avg_views DESC LIMIT 1',
        fetchone=True
    )
    if rows and rows.get('publish_hour') is not None:
        best_hour = int(rows['publish_hour'])
        return best_hour
    return None

def digest_job():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT id, text, rating, message_id, views, reactions FROM posts WHERE status = \'published\' AND published_at >= ? ORDER BY rating DESC LIMIT 5',
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
                        record_publish_time(row['id'], views, reactions)
            except Exception as e:
                print(f"[WARN] Не удалось получить статистику для {row['message_id']}: {e}")
        digest += f"{i}. {short_text}\n   👁 {views} просмотров, ❤️ {reactions} реакций\n\n"

    best_hour = analyze_best_time()
    if best_hour is not None:
        digest += f"\n💡 **Совет:** лучшее время для публикации – {best_hour}:00 МСК (на основе статистики)."
    else:
        digest += "\n💡 Накопите больше данных для анализа времени публикации."

    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": digest, "parse_mode": "Markdown"}, timeout=30)

def weekly_report():
    stats = get_weekly_stats()
    if stats:
        msg = f"📊 Еженедельный отчёт:\nОдобрено: {stats['published']}\nОтклонено: {stats['rejected']}\nВсего создано: {stats['total']}"
    else:
        msg = "📊 Недостаточно данных."
    best_hour = analyze_best_time()
    if best_hour is not None:
        msg += f"\n💡 Лучшее время для публикации: {best_hour}:00 МСК."
    send_message(ADMIN_CHAT_ID, msg)

def job(auto_publish=False, custom_topic=None):
    print(f"[DEBUG] job started at {datetime.now()}")
    send_message(ADMIN_CHAT_ID, f"🔄 Генерация поста начата в {datetime.now().strftime('%H:%M:%S')}")
    check_and_repost()
    print(f"[DEBUG] check_and_repost done")
    print(f"[{datetime.now()}] Генерация поста...")
    try:
        post_text, image_prompt, topic, format_type = generate_post(custom_topic=custom_topic)
        print(f"[DEBUG] post_text получен, длина {len(post_text)}")
        image_path = generate_image(image_prompt)
        print(f"[DEBUG] image_path = {image_path}")
        if not image_path:
            print("[WARN] Картинка не сгенерирована, публикую только текст")
            if auto_publish:
                publish_text_only(post_text)
                print(f"[{datetime.now()}] ✅ Пост без картинки опубликован (авто)")
            else:
                send_for_approval_no_image(post_text, topic, format_type)
            return

        if auto_publish:
            if publish_to_telegram(post_text, image_path):
                print(f"[{datetime.now()}] ✅ Пост опубликован (авто)")
                send_message(ADMIN_CHAT_ID, f"✅ Авто-пост опубликован в {datetime.now().strftime('%H:%M')}")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка авто-публикации")
        else:
            session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
            ok = send_for_approval(post_text, image_path, image_prompt, session_id, topic, format_type)
            if ok:
                print(f"[{datetime.now()}] ✅ Пост отправлен на модерацию")
                send_message(ADMIN_CHAT_ID, "✅ Пост отправлен на модерацию!")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка модерации")
                send_message(ADMIN_CHAT_ID, "❌ Ошибка модерации")
    except Exception as e:
        print(f"[ERROR] job: {e}")
        traceback.print_exc()
        send_message(ADMIN_CHAT_ID, f"❌ Ошибка в job: {str(e)[:100]}")
        raise

# ======================== ВЕБ-СЕРВЕР =========================
def run_job_async():
    try:
        job(auto_publish=False)
    except Exception as e:
        print(f"[ERROR] Асинхронный job: {e}")
        traceback.print_exc()

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            threading.Thread(target=run_job_async, daemon=True).start()
            self.send_response(200)
            self.end_headers()
            self.wfile.write("✅ Генерация поста запущена в фоне. Результат придёт в Telegram через 1-2 минуты.".encode())
            return
        elif self.path == '/test_publish':
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                job(auto_publish=True)
                output = sys.stdout.getvalue()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"✅ Успешно (авто-публикация)!\n\n{output}".encode())
            except Exception as e:
                output = sys.stdout.getvalue()
                error_text = traceback.format_exc()
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"❌ ОШИБКА: {str(e)}\n\n{output}\n\nСТЕК:\n{error_text}".encode())
            finally:
                sys.stdout = old_stdout
            return
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

# ======================== РАСПИСАНИЕ =========================
schedule.every().day.at("15:00").do(lambda: job(auto_publish=False))
schedule.every().day.at("07:00").do(publish_scheduled_posts)
schedule.every().sunday.at("17:00").do(weekly_report)
schedule.every().sunday.at("17:00").do(digest_job)
schedule.every().day.at("03:00").do(backup_db)

print("Бот запущен. Ожидание расписания...")
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}")
print("Модерация каждый день в 18:00 МСК, публикация в 10:00 МСК.")
print("Для админа меню открывается автоматически при любом сообщении.")
print("Ручная генерация: /generate [тема]")
print("Unsplash: " + ("подключён" if UNSPLASH_ACCESS_KEY else "не подключён (используется резерв)"))

while True:
    schedule.run_pending()
    time.sleep(60)