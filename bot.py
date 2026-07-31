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

# ======================== ИМПОРТ МОДУЛЕЙ (ЕСЛИ ОНИ ЕСТЬ) =========================
try:
    from modules import trends
except ImportError:
    trends = None
    print("[WARN] Модуль trends не найден, тренды Google недоступны")

try:
    from modules import image_enhancer
except ImportError:
    image_enhancer = None
    print("[WARN] Модуль image_enhancer не найден, инфографика недоступна")

try:
    from modules import prompt_manager
except ImportError:
    prompt_manager = None
    print("[WARN] Модуль prompt_manager не найден, управление промптами из БД недоступно")

try:
    from modules import financial_api
except ImportError:
    financial_api = None
    print("[WARN] Модуль financial_api не найден, финансовые API недоступны")

try:
    from modules import format_selector
except ImportError:
    format_selector = None
    print("[WARN] Модуль format_selector не найден, выбор формата поста недоступен")

try:
    from modules import qa_collector
except ImportError:
    qa_collector = None
    print("[WARN] Модуль qa_collector не найден, сбор вопросов недоступен")

try:
    from modules import backup
except ImportError:
    backup = None
    print("[WARN] Модуль backup не найден, автоматический бэкап недоступен")

try:
    from modules import monitor
except ImportError:
    monitor = None
    print("[WARN] Модуль monitor не найден, мониторинг состояния недоступен")

try:
    from modules import stats
except ImportError:
    stats = None
    print("[WARN] Модуль stats не найден, расширенная статистика недоступна")

# ======================== КОНФИГУРАЦИЯ =========================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")

API_PROVIDER = os.getenv("API_PROVIDER", "openrouter").lower()
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek/deepseek-chat:free")

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
DB_PATH = "posts.db" if not DATABASE_URL else None

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
            "HTTP-Referer": "https://skeptik-bot.onrender.com"
        }
    }
}

config = PROVIDER_CONFIG.get(API_PROVIDER, PROVIDER_CONFIG["openrouter"])
API_URL = config["url"]
API_HEADERS_FUNC = config["headers"]
API_DEFAULT_MODEL = config["default_model"]
if not MODEL_NAME:
    MODEL_NAME = API_DEFAULT_MODEL

# ======================== УЛУЧШЕНИЯ (FALLBACK) =========================
def get_financial_data(symbol="OZON"):
    if financial_api:
        return financial_api.get_financial_data(symbol)
    return None

def select_format():
    if format_selector:
        return format_selector.select_format()
    weekday = datetime.now().weekday()
    formats = ["мем", "новость", "аналитика", "мем", "аналитика", "новость", "мем"]
    return formats[weekday % len(formats)]

def get_trending_topic():
    if trends:
        return trends.get_trending_topic()
    return None

def create_infographic(data, labels, title="Ключевые показатели"):
    if image_enhancer:
        return image_enhancer.create_infographic(data, labels, title)
    return None

def collect_questions():
    if qa_collector:
        return qa_collector.collect_questions()
    print("[INFO] Сбор вопросов не настроен")

def publish_answers():
    if qa_collector:
        return qa_collector.publish_answers()
    print("[INFO] Публикация ответов на вопросы не настроена")

def backup_db():
    if backup:
        return backup.backup_db()
    try:
        if not os.path.exists("backups"):
            os.makedirs("backups")
        import shutil
        src = "posts.db" if not DATABASE_URL else None
        if src and os.path.exists(src):
            dst = f"backups/posts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            shutil.copyfile(src, dst)
            print(f"[INFO] Бэкап создан: {dst}")
    except Exception as e:
        print(f"[ERROR] Ошибка бэкапа: {e}")

def check_health():
    if monitor:
        return monitor.check_health()
    try:
        resp = requests.get("https://skeptik-bot.onrender.com/", timeout=10)
        if resp.status_code != 200:
            send_message(ADMIN_CHAT_ID, f"⚠️ Бот не отвечает! Статус: {resp.status_code}")
    except Exception as e:
        send_message(ADMIN_CHAT_ID, f"❌ Ошибка мониторинга: {e}")

def get_prompt(name='system_prompt'):
    if prompt_manager:
        return prompt_manager.get_prompt(name)
    row = execute_query('SELECT content FROM prompts WHERE name = ?', (name,), fetchone=True)
    return row[0] if row else None

def set_prompt(name, content):
    if prompt_manager:
        return prompt_manager.set_prompt(name, content)
    execute_query('REPLACE INTO prompts (name, content) VALUES (?, ?)', (name, content))

def update_stats():
    if stats:
        return stats.update_stats()
    rows = execute_query(
        'SELECT session_id, message_id FROM posts WHERE status = \'published\' AND message_id IS NOT NULL AND views = 0',
        fetch=True
    )
    for row in rows:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMessageStatistics"
            params = {"chat_id": TELEGRAM_CHAT_ID, "message_id": row['message_id']}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('ok'):
                    stats_data = data.get('result', {})
                    views = stats_data.get('views', 0)
                    reactions = sum(r.get('count', 0) for r in stats_data.get('reactions', []))
                    execute_query(
                        'UPDATE posts SET views = ?, reactions = ? WHERE session_id = ?',
                        (views, reactions, row['session_id'])
                    )
                    execute_query(
                        'INSERT INTO publish_times (post_id, publish_hour, publish_weekday, views, reactions) '
                        'SELECT id, strftime("%H", created_at), strftime("%w", created_at), ?, ? FROM posts WHERE session_id = ?',
                        (views, reactions, row['session_id'])
                    )
        except Exception as e:
            print(f"[ERROR] Ошибка обновления статистики: {e}")

# ======================== БАЗА ДАННЫХ =========================
def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    else:
        return sqlite3.connect(DB_PATH)

def execute_query(query, params=None, fetch=False, fetchone=False):
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL:
        query = query.replace('?', '%s')
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

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL:
        # PostgreSQL
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
                reactions INTEGER,
                FOREIGN KEY(post_id) REFERENCES posts(id)
            )
        ''')
        default_prompt = (
            "Ты — автор канала «Скептик с EBITDA».\n"
            "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
            "НЕ выводи <think>, рассуждения — только готовый пост.\n"
            "Используй только актуальные данные (2023–2026 год).\n"
            "Структура поста (ОБЯЗАТЕЛЬНО):\n"
            "1. Заголовок с эмодзи (например, 🚨).\n"
            "2. Каждый новый смысловой блок начинай с эмодзи (📉, 🏬, 💰, ⚠️).\n"
            "3. Ставь двойной перенос между абзацами.\n"
            "4. Ключевые цифры выделяй жирным через **...** (например, **17.2 млрд**).\n"
            "5. В конце — Action Item с ✅ (отдельно).\n"
            "6. После Action Item — источник и хештеги (#тег1 #тег2).\n"
            "7. Не используй разделители вроде '---'.\n"
            "8. Включи цитату из отчёта или интервью топ-менеджера.\n"
            "После текста === и описание картинки (англ., 3–4 слова)."
        )
        cur.execute('''
            INSERT INTO prompts (name, content) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING
        ''', ('system_prompt', default_prompt))
    else:
        # SQLite
        cur.execute('''
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                publish_hour INTEGER,
                publish_weekday INTEGER,
                views INTEGER,
                reactions INTEGER,
                FOREIGN KEY(post_id) REFERENCES posts(id)
            )
        ''')
        cur.execute('''
            INSERT OR IGNORE INTO prompts (name, content) VALUES (?, ?)
        ''', ('system_prompt', default_prompt))
    conn.commit()
    cur.close()
    conn.close()

init_db()

# ======================== ГЕНЕРАЦИЯ ПОСТА =========================
def generate_post():
    topic = get_trending_topic()
    if not topic:
        topic = get_topic_by_analytics()
    print(f"[DEBUG] Выбрана тема: {topic}")

    fmt = select_format()
    style = {
        "мем": "Саркастичный, с юмором, короткий (до 300 символов).",
        "новость": "Информативный, факты, даты, цитаты.",
        "аналитика": "Глубокий разбор цифр, трендов, выводы."
    }.get(fmt, "Дерзкий, саркастичный, с реальными цифрами.")

    financials = get_financial_data("OZON")
    financial_text = ""
    if financials:
        financial_text = f"Используй актуальные цифры: выручка {financials['revenue']}, прибыль {financials['profit']}, EBITDA {financials['ebitda']}."

    system_content = get_prompt('system_prompt')
    if not system_content:
        system_content = default_prompt

    headers = API_HEADERS_FUNC(DEEPSEEK_API_KEY)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Напиши пост на тему: {topic}. {financial_text} Стиль: {style}"}
        ],
        "temperature": 0.85,
        "max_tokens": 700
    }

    for attempt in range(3):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=90)
            if response.status_code != 200:
                raise Exception(f"API вернул {response.status_code}: {response.text}")
            data = response.json()
            if "choices" not in data:
                raise Exception("Нет choices")
            full_text = data["choices"][0]["message"]["content"]
            if not full_text:
                raise Exception("Пустой ответ")
            full_text = clean_text(full_text)
            if not full_text.endswith(('.', '?', '!', '"', ')')):
                full_text += "... (продолжение в следующем посте)"
            if "===" in full_text:
                parts = full_text.split("===", 1)
                post_text = parts[0].strip()
                image_prompt = parts[1].strip() if len(parts) > 1 else ""
            else:
                post_text = full_text.strip()
                image_prompt = ""
            if len(image_prompt) < 10:
                image_prompt = "retail comparison illustration, business graph, sarcastic, modern, colorful"
            post_text = beautify_post(post_text)
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

def get_topic_by_analytics():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        'SELECT topic, rating, views, reactions FROM posts WHERE status = \'published\' AND published_at >= ? AND topic IS NOT NULL AND topic != \'\'',
        (week_ago,), fetch=True
    )
    if not rows:
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
    return max(topic_stats, key=topic_stats.get)

def get_topic_from_news():
    rss_urls = [
        "https://www.rbc.ru/rss/",
        "https://www.kommersant.ru/RSS/news.xml",
        "https://lenta.ru/rss/news"
    ]
    keywords = ["ozon", "wildberries", "магнит", "ритейл", "торговля", "нефть", "лукойл"]
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

# ======================== ОСТАЛЬНЫЕ ВСПОМОГАТЕЛЬНЫЕ =========================
def clean_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

def beautify_post(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
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
        if i+1 < len(sentences):
            paragraphs.append(sentences[i] + ' ' + sentences[i+1])
            i += 2
        else:
            paragraphs.append(sentences[i])
            i += 1
    text = '\n\n'.join(paragraphs)
    def replacer(m):
        num = m.group(0)
        if not re.search(r'\*\*.*?' + re.escape(num) + r'.*?\*\*', text):
            return f'**{num}**'
        return num
    text = re.sub(r'\b(\d+[.,]?\d*)\b', replacer, text)
    if action_text:
        hashtag_match = re.search(r'(#\w+(?:\s*#\w+)*)$', text)
        if hashtag_match:
            hashtags = hashtag_match.group(1)
            text = text.replace(hashtags, '').strip()
            text = text + f'\n\n**{action_text}**\n\n{hashtags}'
        else:
            text = text + f'\n\n**{action_text}**'
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

def generate_image(prompt, image_prompt=None, extra_data=None):
    if extra_data and isinstance(extra_data, dict):
        data = extra_data.get('data')
        labels = extra_data.get('labels')
        if data and labels:
            img_path = create_infographic(data, labels)
            if img_path:
                return img_path
    if len(prompt) > 100:
        prompt = prompt[:100]
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
                        raise Exception("Слишком маленькое изображение")
                    img = img.convert('L')
                    pixels = list(img.getdata())
                    avg = sum(pixels) / len(pixels)
                    if avg < 30:
                        print("[WARN] Обнаружено чёрное изображение, пробуем с упрощённым промптом")
                        os.remove("temp_image.jpg")
                        prompt = "retail illustration, business, comparison, sarcastic, modern"
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
        url = f"https://image.pollinations.ai/prompt/business%20illustration?width=1200&height=800&seed={random.randint(1,999999)}&t={int(time.time())}"
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
def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"[ERROR] send_message: {e}")

def save_post(session_id, text, image_path, image_prompt, topic):
    execute_query(
        'INSERT OR REPLACE INTO posts (session_id, text, image_path, image_prompt, topic, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (session_id, text, image_path, image_prompt, topic, 'pending', datetime.now().isoformat())
    )

def get_post(session_id):
    row = execute_query(
        'SELECT text, image_path, image_prompt, status, scheduled_publish_time, edit_pending, rating, reposted, message_id, topic FROM posts WHERE session_id = ?',
        (session_id,), fetchone=True
    )
    return row

def update_post_status(session_id, status, scheduled_time=None):
    if scheduled_time:
        execute_query(
            'UPDATE posts SET status = ?, scheduled_publish_time = ?, approved_at = ? WHERE session_id = ?',
            (status, scheduled_time.isoformat(), datetime.now().isoformat(), session_id)
        )
    else:
        execute_query('UPDATE posts SET status = ? WHERE session_id = ?', (status, session_id))

def send_for_approval_no_image(post_text, topic):
    session_id = f"{int(time.time())}_{random.randint(1000,9999)}"
    save_post(session_id, post_text, "", "", topic)
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
    for i, part in enumerate(parts, 1):
        if len(parts) == 1:
            text_data = {"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML"}
        else:
            text_data = {"chat_id": TELEGRAM_CHAT_ID, "text": f"📝 Пост (часть {i}/{len(parts)}):\n\n{part}", "parse_mode": "HTML"}
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=text_data, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка отправки текста (часть {i}): {resp.text}")
            return False
    return True

def send_for_approval(post_text, image_path, image_prompt, session_id, topic):
    save_post(session_id, post_text, image_path, image_prompt, topic)
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

# ======================== ОБРАБОТЧИК КОМАНД АДМИНА =========================
def handle_admin_command(text, chat_id):
    if text.startswith('/stats'):
        rows = execute_query(
            'SELECT COUNT(*) as total, SUM(CASE WHEN status=\'published\' THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status=\'rejected\' THEN 1 ELSE 0 END) as rejected FROM posts',
            fetchone=True
        )
        msg = f"📊 Статистика:\nВсего постов: {rows['total']}\nОпубликовано: {rows['published']}\nОтклонено: {rows['rejected']}"
        send_message(chat_id, msg)
    elif text.startswith('/setprompt'):
        new_prompt = text.replace('/setprompt', '').strip()
        if new_prompt:
            set_prompt('system_prompt', new_prompt)
            send_message(chat_id, "✅ Промпт обновлён!")
        else:
            send_message(chat_id, "❌ Напиши новый промпт после команды")
    elif text.startswith('/getprompt'):
        current = get_prompt('system_prompt')
        send_message(chat_id, f"Текущий промпт:\n\n{current}")
    elif text.startswith('/generate'):
        send_message(chat_id, "🔄 Запускаю генерацию...")
        job(auto_publish=False)
    elif text.startswith('/backup'):
        backup_db()
        send_message(chat_id, "✅ Бэкап создан")
    elif text.startswith('/health'):
        check_health()
        send_message(chat_id, "✅ Проверка здоровья выполнена")

# ======================== ПОЛЛИНГ ОБНОВЛЕНИЙ =========================
def poll_updates():
    offset = 0
    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["callback_query", "message"]},
                timeout=35
            )
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
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb["id"], "text": "Обрабатываю..."},
                                timeout=10
                            )
                        except:
                            pass
                elif "message" in update and update["message"].get("chat", {}).get("id") == int(ADMIN_CHAT_ID):
                    chat_id = update["message"]["chat"]["id"]
                    text = update["message"].get("text", "")
                    if text.startswith('/'):
                        handle_admin_command(text, chat_id)
                    elif chat_id in edit_mode:
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

# ======================== ПРОЦЕСС КНОПОК =========================
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
            new_img = generate_image(new_prompt, extra_data=None)
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
            new_img = generate_image(new_prompt, extra_data=None)
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

def delete_post(session_id):
    execute_query('DELETE FROM posts WHERE session_id = ?', (session_id,))

def update_post_text(session_id, new_text):
    execute_query('UPDATE posts SET text = ? WHERE session_id = ?', (new_text, session_id))

# ======================== ОСНОВНАЯ ЗАДАЧА =========================
def job(auto_publish=False):
    print(f"[DEBUG] job started at {datetime.now()}")
    send_message(ADMIN_CHAT_ID, f"🔄 Генерация поста начата в {datetime.now().strftime('%H:%M:%S')}")
    if not os.path.exists("backups"):
        backup_db()
    update_stats()
    check_and_repost()
    print(f"[{datetime.now()}] Генерация поста...")
    try:
        post_text, image_prompt, topic = generate_post()
        print(f"[DEBUG] post_text получен, длина {len(post_text)}")
        financials = get_financial_data("OZON")
        extra_data = None
        if financials:
            try:
                extra_data = {
                    "data": [float(financials['revenue'].replace(',', '')) if isinstance(financials['revenue'], str) else financials['revenue'],
                              float(financials['profit'].replace(',', '')) if isinstance(financials['profit'], str) else financials['profit'],
                              float(financials['ebitda'].replace(',', '')) if isinstance(financials['ebitda'], str) else financials['ebitda']],
                    "labels": ["Выручка", "Прибыль", "EBITDA"]
                }
            except:
                extra_data = None
        image_path = generate_image(image_prompt, extra_data=extra_data)
        print(f"[DEBUG] image_path = {image_path}")
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
                send_message(ADMIN_CHAT_ID, "✅ Пост отправлен на модерацию!")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка модерации")
                send_message(ADMIN_CHAT_ID, "❌ Ошибка модерации")
    except Exception as e:
        print(f"[ERROR] job: {e}")
        traceback.print_exc()
        send_message(ADMIN_CHAT_ID, f"❌ Ошибка в job: {str(e)[:100]}")
        raise

def publish_text_only(text):
    parts = split_into_parts(text, max_len=1000)
    for part in parts:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML"}
        resp = requests.post(url, json=data, timeout=30)
        if resp.status_code != 200:
            return False
    return True

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

def publish_scheduled_posts():
    print(f"[{datetime.now()}] Проверка запланированных постов...")
    now = datetime.now().isoformat()
    rows = execute_query(
        'SELECT session_id, text, image_path FROM posts WHERE status = \'approved\' AND scheduled_publish_time <= ?',
        (now,), fetch=True
    )
    for p in rows:
        if publish_to_telegram(p["text"], p["image_path"], p["session_id"]):
            update_post_status(p["session_id"], 'published')
            print(f"[{datetime.now()}] ✅ Опубликован {p['session_id']}")
        else:
            if publish_text_only(p["text"]):
                update_post_status(p["session_id"], 'published')
                print(f"[{datetime.now()}] ✅ Опубликован текст {p['session_id']}")
            else:
                print(f"[{datetime.now()}] ❌ Ошибка публикации {p['session_id']}")

# ======================== ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ =========================
def weekly_report():
    stats = execute_query(
        'SELECT COUNT(*) as total, SUM(CASE WHEN status=\'published\' THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status=\'rejected\' THEN 1 ELSE 0 END) as rejected FROM posts WHERE created_at >= ?',
        (datetime.now() - timedelta(days=7)).isoformat(), fetchone=True
    )
    msg = f"📊 Еженедельный отчёт:\nОдобрено: {stats['published']}\nОтклонено: {stats['rejected']}\nВсего создано: {stats['total']}"
    send_message(ADMIN_CHAT_ID, msg)

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
                self.wfile.write(f"✅ Успешно (модерация)!\n\n{output}".encode())
            except Exception as e:
                output = sys.stdout.getvalue()
                error_text = traceback.format_exc()
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"❌ ОШИБКА: {str(e)}\n\n{output}\n\nСТЕК:\n{error_text}".encode())
            finally:
                sys.stdout = old_stdout
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

# ======================== РАСПИСАНИЕ И ЗАПУСК =========================
schedule.every().day.at("15:00").do(lambda: job(auto_publish=False))  # 18:00 МСК
schedule.every().day.at("07:00").do(publish_scheduled_posts)          # 10:00 МСК
schedule.every().sunday.at("17:00").do(weekly_report)
schedule.every().day.at("03:00").do(backup_db)
schedule.every().sunday.at("20:00").do(collect_questions)
schedule.every().wednesday.at("10:00").do(publish_answers)
schedule.every().hour.do(update_stats)

print("Бот запущен. Ожидание расписания...")
print(f"Провайдер: {API_PROVIDER}, Модель: {MODEL_NAME}")
print("Модерация каждый день в 18:00 МСК, публикация в 10:00 МСК.")

while True:
    schedule.run_pending()
    time.sleep(60)