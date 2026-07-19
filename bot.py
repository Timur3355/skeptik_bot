import requests, time, schedule, os, threading, sys, io, traceback, json, random, re
from datetime import datetime, timedelta
import urllib.request, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import pytz, feedparser

# ====== КОНФИГ ======
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

# ====== БАЗА ДАННЫХ ======
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

def get_post(session_id):
    return execute_query('SELECT text, image_path, image_prompt, status, scheduled_publish_time, edit_pending, rating, reposted, message_id, topic FROM posts WHERE session_id = ?', (session_id,), fetchone=True)

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
    return execute_query(
        "SELECT session_id, text, image_path FROM posts WHERE status = 'approved' AND scheduled_publish_time <= ?",
        (now,), fetch=True
    )

def get_weekly_stats():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    return execute_query(
        "SELECT COUNT(*) as total, SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as published, SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected FROM posts WHERE created_at >= ?",
        (week_ago,), fetchone=True
    )

# ====== ВСПОМОГАТЕЛЬНЫЕ ======
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
        return True
    except Exception as e:
        print(f"[WARN] Не удалось создать заглушку через Pillow: {e}")
        return False

def get_topic_from_news():
    rss_urls = ["https://www.rbc.ru/rss/", "https://www.kommersant.ru/RSS/news.xml", "https://lenta.ru/rss/news"]
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
    except:
        return DAY_TOPICS.get(datetime.now().weekday(), DAY_TOPICS[0])

def get_topic_by_analytics():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        "SELECT topic, rating, views, reactions FROM posts WHERE status = 'published' AND published_at >= ? AND topic IS NOT NULL AND topic != ''",
        (week_ago,), fetch=True
    )
    if not rows:
        return get_topic_from_news()
    topic_stats = {}
    for row in rows:
        topic = row['topic']
        score = (row['rating'] or 0) + (row['views'] or 0)*0.1 + (row['reactions'] or 0)*0.5
        topic_stats[topic] = topic_stats.get(topic, 0) + score
    if not topic_stats:
        return get_topic_from_news()
    return max(topic_stats, key=topic_stats.get)

# ====== ГЕНЕРАЦИЯ ======
def generate_post():
    topic = get_topic_by_analytics()
    print(f"[DEBUG] Тема: {topic}")
    headers = API_HEADERS_FUNC(DEEPSEEK_API_KEY)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": (
                "Ты — автор канала «Скептик с EBITDA».\n"
                "Стиль: дерзкий, саркастичный, с реальными цифрами.\n"
                "НЕ выводи <think>, рассуждения — только пост.\n"
                "Пост: 4–5 абзацев, 600–800 символов.\n"
                "Используй эмодзи в начале абзацев, НЕ используй HTML.\n"
                "В конце — Action Item с ✅.\n"
                "Указывай период и источник.\n"
                "После Action Item добавь ссылку на источник.\n"
                "Добавь 3–5 хештегов #.\n"
                "После текста === и описание картинки (англ., 3–4 слова)."
            )},
            {"role": "user", "content": f"Напиши пост на тему: {topic}. Используй реальные цифры из отчётов."}
        ],
        "temperature": 0.85,
        "max_tokens": 250
    }
    for _ in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                raise Exception(f"API {resp.status_code}")
            data = resp.json()
            full = data["choices"][0]["message"]["content"]
            full = clean_text(full)
            if "===" in full:
                post_text, image_prompt = full.split("===", 1)
                post_text = post_text.strip()
                image_prompt = image_prompt.strip()
            else:
                post_text = full.strip()
                image_prompt = ""
            if len(image_prompt) < 10:
                first = post_text.split('.')[0] if '.' in post_text else post_text[:50]
                image_prompt = f"{first}, business finance illustration, sarcastic, modern"
            return post_text, image_prompt, topic
        except:
            time.sleep(5)
    raise Exception("Не удалось получить ответ от API")

def generate_image(prompt):
    if len(prompt) > 150:
        prompt = prompt[:150]
    services = [
        {"name": "Pollinations", "url": lambda p: f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p + str(random.randint(1,100000)))}?width=1200&height=800&seed={random.randint(1,999999)}&t={int(time.time())}", "timeout": 90},
        {"name": "Lexica", "url": lambda p: f"https://lexica.art/api/v1/search?q={urllib.parse.quote(p)}", "timeout": 30, "parse": lambda d: d.get("images", [{}])[0].get("src") if d.get("images") else None},
        {"name": "Fallback", "local": True, "path": "fallback.jpg"}
    ]
    for s in services:
        try:
            if s.get("local"):
                if os.path.exists(s["path"]):
                    return s["path"]
                # Пытаемся создать через Pillow
                if create_fallback_image():
                    return "fallback.jpg"
                # Если не вышло – скачиваем заглушку из интернета
                try:
                    r = requests.get("https://via.placeholder.com/1200x800/000000/FFFFFF?text=Изображение+недоступно", timeout=30)
                    if r.status_code == 200:
                        with open("fallback.jpg", "wb") as f:
                            f.write(r.content)
                        return "fallback.jpg"
                except:
                    pass
                # Крайний случай: создаём пустой файл (не картинка, но лучше чем ничего)
                open("fallback.jpg", "a").close()
                return "fallback.jpg"
            print(f"[DEBUG] Пробуем {s['name']}...")
            url = s["url"](prompt) if callable(s["url"]) else s["url"]
            r = requests.get(url, timeout=s.get("timeout", 60))
            if r.status_code == 200:
                if "parse" in s:
                    img_url = s["parse"](r.json())
                    if img_url:
                        ir = requests.get(img_url, timeout=30)
                        if ir.status_code == 200:
                            with open("temp_image.jpg", "wb") as f:
                                f.write(ir.content)
                            return "temp_image.jpg"
                else:
                    with open("temp_image.jpg", "wb") as f:
                        f.write(r.content)
                    return "temp_image.jpg"
            else:
                print(f"[WARN] {s['name']} вернул {r.status_code}")
        except Exception as e:
            print(f"[WARN] {s['name']}: {e}")
        time.sleep(2)
    # Если всё провалилось, возвращаем fallback (он должен быть создан)
    if os.path.exists("fallback.jpg"):
        return "fallback.jpg"
    else:
        # Скачиваем прямо сейчас
        try:
            r = requests.get("https://via.placeholder.com/1200x800/000000/FFFFFF?text=Изображение+недоступно", timeout=30)
            if r.status_code == 200:
                with open("fallback.jpg", "wb") as f:
                    f.write(r.content)
                return "fallback.jpg"
        except:
            pass
        return None

# ====== ПУБЛИКАЦИЯ ======
def publish_text_only(text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30)
        return r.status_code == 200
    except:
        return False

def send_for_approval_no_image(post_text, topic):
    sid = f"{int(time.time())}_{random.randint(1000,9999)}"
    save_post(sid, post_text, "", "", topic)
    parts = split_text(post_text, 3000)
    for i, part in enumerate(parts, 1):
        data = {
            "chat_id": ADMIN_CHAT_ID,
            "text": f"📝 Новый пост на проверку (без картинки, часть {i}/{len(parts)}):\n\n{part}",
            "reply_markup": json.dumps({
                "inline_keyboard": [[
                    {"text": "✅ Одобрить", "callback_data": f"approve_{sid}"},
                    {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{sid}"},
                    {"text": "✏️ Редактировать", "callback_data": f"edit_{sid}"},
                    {"text": "❌ Отклонить", "callback_data": f"reject_{sid}"}
                ]]
            })
        }
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=data, timeout=30)
        if r.status_code != 200:
            return False
    return True

def publish_to_telegram(text, image_path, session_id=None):
    try:
        if not os.path.exists(image_path):
            return False
        parts = split_text(text, 1000)
        first = parts[0] if parts else ""
        second = parts[1] if len(parts) > 1 else ""
        # Проверка прав
        check = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember",
                             params={"chat_id": TELEGRAM_CHAT_ID, "user_id": "me"}, timeout=10)
        if check.status_code == 200 and check.json().get("result", {}).get("status") not in ["administrator", "creator"]:
            return False
        # Фото
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": first}
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", files=files, data=data, timeout=30)
            if r.status_code != 200:
                return False
            if session_id:
                msg_data = r.json()
                mid = msg_data.get('result', {}).get('message_id')
                if mid:
                    execute_query('UPDATE posts SET message_id = ? WHERE session_id = ?', (mid, session_id))
        if second:
            r2 = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                               json={"chat_id": TELEGRAM_CHAT_ID, "text": f"📎 Продолжение:\n\n{second}"}, timeout=30)
            if r2.status_code != 200:
                return False
        return True
    except:
        return False

def send_for_approval(post_text, image_path, image_prompt, session_id, topic):
    save_post(session_id, post_text, image_path, image_prompt, topic)
    first_part = split_text(post_text, 1000)[0]
    caption = f"📝 Новый пост на проверку (начало):\n\n{first_part}..."
    try:
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {
                "chat_id": ADMIN_CHAT_ID,
                "caption": caption,
                "reply_markup": json.dumps({
                    "inline_keyboard": [[
                        {"text": "✅ Одобрить", "callback_data": f"approve_{session_id}"},
                        {"text": "🔄 Перегенерировать", "callback_data": f"regenerate_{session_id}"},
                        {"text": "✏️ Редактировать", "callback_data": f"edit_{session_id}"},
                        {"text": "❌ Отклонить", "callback_data": f"reject_{session_id}"}
                    ]]
                })
            }
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", files=files, data=data, timeout=30)
            if r.status_code != 200:
                return False
        full_parts = split_text(post_text, 3000)
        for i, part in enumerate(full_parts, 1):
            r2 = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                               json={"chat_id": ADMIN_CHAT_ID, "text": f"📄 Полный текст поста (часть {i}/{len(full_parts)}):\n\n{part}"}, timeout=30)
            if r2.status_code != 200:
                return False
        return True
    except:
        return False

def schedule_publish(session_id):
    now = datetime.now(MOSCOW_TZ)
    pub = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= pub:
        pub += timedelta(days=1)
    update_post_status(session_id, 'approved', scheduled_time=pub)
    send_message(ADMIN_CHAT_ID, f"✅ Пост запланирован на {pub.strftime('%d.%m.%Y %H:%M')} МСК.")

def send_message(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

# ====== АВТОПОВТОР ======
def check_and_repost():
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    rows = execute_query(
        "SELECT session_id, text FROM posts WHERE status = 'published' AND reposted = FALSE AND rating >= 3 AND published_at <= ?",
        (cutoff,), fetch=True
    )
    for row in rows:
        if publish_text_only(row['text']):
            execute_query('UPDATE posts SET reposted = TRUE WHERE session_id = ?', (row['session_id'],))

# ====== ДАЙДЖЕСТ ======
def digest_job():
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    rows = execute_query(
        "SELECT text, rating, message_id, views, reactions FROM posts WHERE status = 'published' AND published_at >= ? ORDER BY rating DESC LIMIT 5",
        (week_ago,), fetch=True
    )
    if not rows:
        send_message(ADMIN_CHAT_ID, "📊 За неделю нет постов.")
        return
    digest = "📅 **Лучшие посты недели:**\n\n"
    for i, row in enumerate(rows, 1):
        short = row['text'][:150] + "..." if len(row['text']) > 150 else row['text']
        views = row['views'] or 0
        reactions = row['reactions'] or 0
        if row['message_id'] and views == 0:
            try:
                r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMessageStatistics",
                                 params={"chat_id": TELEGRAM_CHAT_ID, "message_id": row['message_id']}, timeout=10)
                if r.status_code == 200:
                    stats = r.json().get('result', {})
                    views = stats.get('views', 0)
                    reactions = sum(r.get('count', 0) for r in stats.get('reactions', []))
                    execute_query('UPDATE posts SET views = ?, reactions = ? WHERE message_id = ?', (views, reactions, row['message_id']))
            except:
                pass
        digest += f"{i}. {short}\n   👁 {views} просмотров, ❤️ {reactions} реакций\n\n"
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": digest, "parse_mode": "Markdown"}, timeout=30)

def weekly_report():
    stats = get_weekly_stats()
    if stats:
        msg = f"📊 Еженедельный отчёт:\nОдобрено: {stats['published']}\nОтклонено: {stats['rejected']}\nВсего создано: {stats['total']}"
    else:
        msg = "📊 Недостаточно данных."
    send_message(ADMIN_CHAT_ID, msg)

# ====== ОСНОВНАЯ ЗАДАЧА ======
def job(auto_publish=False):
    check_and_repost()
    print(f"[{datetime.now()}] Генерация поста...")
    try:
        post_text, image_prompt, topic = generate_post()
        image_path = generate_image(image_prompt)
        if not image_path:
            print("[WARN] Картинка не сгенерирована")
            if auto_publish:
                publish_text_only(post_text)
            else:
                send_for_approval_no_image(post_text, topic)
            return
        if auto_publish:
            if publish_to_telegram(post_text, image_path):
                send_message(ADMIN_CHAT_ID, f"✅ Авто-пост в {datetime.now().strftime('%H:%M')}")
        else:
            sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            ok = send_for_approval(post_text, image_path, image_prompt, sid, topic)
            if ok:
                print("[OK] Пост на модерации")
            else:
                print("[ERROR] Ошибка модерации")
    except Exception as e:
        print(f"[ERROR] job: {e}")
        traceback.print_exc()

# ====== ПУБЛИКАЦИЯ ЗАПЛАНИРОВАННЫХ ======
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

# ====== ОБРАБОТЧИК КНОПОК ======
edit_mode = {}

def process_callback(cb_data, chat_id, msg_id):
    action, sid = cb_data.split('_', 1)
    if action == "rate_up":
        execute_query('UPDATE posts SET rating = rating + 1 WHERE session_id = ?', (sid,))
        answer_callback(chat_id, msg_id, "👍")
        return
    elif action == "rate_down":
        execute_query('UPDATE posts SET rating = rating - 1 WHERE session_id = ?', (sid,))
        answer_callback(chat_id, msg_id, "👎")
        return
    post = get_post(sid)
    if not post:
        answer_callback(chat_id, msg_id, "🔄 Черновик устарел, генерирую новый...")
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_img = generate_image(new_prompt)
            if not new_img:
                send_for_approval_no_image(new_text, new_topic)
                answer_callback(chat_id, msg_id, "✅ Новый пост (без картинки)")
                return
            new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic)
            answer_callback(chat_id, msg_id, "✅ Новый пост на проверку.")
        except:
            answer_callback(chat_id, msg_id, "❌ Ошибка")
        return
    if post["status"] in ("published", "rejected", "approved"):
        answer_callback(chat_id, msg_id, f"ℹ️ Пост уже {post['status']}.")
        return
    if action == "approve":
        schedule_publish(sid)
        answer_callback(chat_id, msg_id, "✅ Одобрен, будет опубликован в 10:00 МСК.")
    elif action == "regenerate":
        answer_callback(chat_id, msg_id, "🔄 Генерирую новый...")
        try:
            new_text, new_prompt, new_topic = generate_post()
            new_img = generate_image(new_prompt)
            if not new_img:
                send_for_approval_no_image(new_text, new_topic)
                delete_post(sid)
                answer_callback(chat_id, msg_id, "🔄 Новый пост (без картинки)")
                return
            new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
            delete_post(sid)
            send_for_approval(new_text, new_img, new_prompt, new_sid, new_topic)
            answer_callback(chat_id, msg_id, "🔄 Новый пост отправлен.")
        except:
            answer_callback(chat_id, msg_id, "❌ Ошибка")
    elif action == "edit":
        answer_callback(chat_id, msg_id, "✏️ Пришли новый текст поста.")
        edit_mode[chat_id] = sid
    elif action == "reject":
        update_post_status(sid, 'rejected')
        answer_callback(chat_id, msg_id, "❌ Отклонён.")

def answer_callback(chat_id, msg_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

# ====== ПОЛЛИНГ ======
def poll_updates():
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 30, "allowed_updates": ["callback_query", "message"]}, timeout=35)
            if r.status_code != 200:
                time.sleep(5)
                continue
            data = r.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    cb = upd["callback_query"]
                    cb_data = cb.get("data")
                    if cb_data:
                        chat_id = cb["message"]["chat"]["id"]
                        msg_id = cb["id"]
                        process_callback(cb_data, chat_id, msg_id)
                        try:
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                          json={"callback_query_id": cb["id"], "text": "Обрабатываю..."}, timeout=10)
                        except:
                            pass
                elif "message" in upd and upd["message"].get("chat", {}).get("id") == int(ADMIN_CHAT_ID):
                    chat_id = upd["message"]["chat"]["id"]
                    if chat_id in edit_mode:
                        sid = edit_mode.pop(chat_id)
                        new_text = upd["message"].get("text")
                        if new_text:
                            update_post_text(sid, new_text)
                            post = get_post(sid)
                            if post:
                                new_sid = f"{int(time.time())}_{random.randint(1000,9999)}"
                                save_post(new_sid, new_text, post["image_path"], post["image_prompt"], post["topic"])
                                delete_post(sid)
                                send_for_approval(new_text, post["image_path"], post["image_prompt"], new_sid, post["topic"])
                                send_message(chat_id, "✅ Пост обновлён")
                            else:
                                send_message(chat_id, "❌ Не удалось найти пост.")
                        else:
                            send_message(chat_id, "❌ Текст не может быть пустым.")
        except:
            time.sleep(5)

# ====== ВЕБ-СЕРВЕР ======
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                job(False)
                out = sys.stdout.getvalue()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"✅ Успешно (модерация)!\n\n{out}".encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"❌ Ошибка: {str(e)}".encode())
            finally:
                sys.stdout = old
        elif self.path == '/test_publish':
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                job(True)
                out = sys.stdout.getvalue()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"✅ Успешно (авто)!\n\n{out}".encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"❌ Ошибка: {str(e)}".encode())
            finally:
                sys.stdout = old
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=start_health_server, daemon=True).start()

# ====== САМОПИНГ ======
def keep_alive():
    while True:
        try:
            urllib.request.urlopen("https://skeptik-bot.onrender.com", timeout=10)
        except:
            pass
        time.sleep(600)

threading.Thread(target=keep_alive, daemon=True).start()
threading.Thread(target=poll_updates, daemon=True).start()

# ====== РАСПИСАНИЕ ======
schedule.every().day.at("06:55").do(lambda: job(False))
schedule.every().day.at("07:00").do(publish_scheduled_posts)
schedule.every().sunday.at("17:00").do(weekly_report)
schedule.every().sunday.at("17:00").do(digest_job)

print("Бот запущен. Ожидание расписания...")
while True:
    schedule.run_pending()
    time.sleep(60)