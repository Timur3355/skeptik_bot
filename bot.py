import os
import threading
import requests
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

print(f"[START] ADMIN_CHAT_ID = {ADMIN_CHAT_ID}")
print(f"[START] TELEGRAM_BOT_TOKEN = {TELEGRAM_BOT_TOKEN[:10] if TELEGRAM_BOT_TOKEN else 'None'}...")
print(f"[START] DEEPSEEK_API_KEY = {DEEPSEEK_API_KEY[:10] if DEEPSEEK_API_KEY else 'None'}...")

def send_message(chat_id, text):
    print(f"[SEND] Отправка сообщения в {chat_id}, длина текста {len(text)}")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        resp = requests.post(url, json=data, timeout=30)
        print(f"[SEND] Статус: {resp.status_code}")
        print(f"[SEND] Ответ: {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[SEND] Ошибка: {e}")
        return False

def generate_post():
    print("[GEN] Начало генерации поста...")
    if not DEEPSEEK_API_KEY:
        print("[GEN] ОШИБКА: DEEPSEEK_API_KEY не задан!")
        return None
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "HTTP-Referer": "https://skeptik-bot.onrender.com",
            "X-Title": "Скептик с EBITDA"
        }
        payload = {
            "model": "deepseek/deepseek-chat:free",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты — автор канала «Скептик с EBITDA». "
                        "Стиль: дерзкий, саркастичный, с реальными цифрами. "
                        "Пост должен быть 3–4 абзаца, примерно 400–600 символов. "
                        "Используй эмодзи, НЕ используй HTML. "
                        "В конце — Action Item с ✅."
                    )
                },
                {
                    "role": "user",
                    "content": "Напиши пост про ошибки российских ритейлеров (Ozon, Wildberries или Магнит). Используй реальные цифры из отчётов."
                }
            ],
            "temperature": 0.85,
            "max_tokens": 400
        }
        print("[GEN] Отправка запроса к OpenRouter...")
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        print(f"[GEN] Статус API: {response.status_code}")
        print(f"[GEN] Ответ API: {response.text[:200]}...")
        if response.status_code == 200:
            data = response.json()
            post_text = data["choices"][0]["message"]["content"]
            print(f"[GEN] Пост получен, длина {len(post_text)}")
            return post_text
        else:
            print(f"[GEN] Ошибка API: {response.text}")
            return None
    except Exception as e:
        print(f"[GEN] Исключение: {e}")
        return None

def send_for_approval(post_text):
    print("[APP] Отправка на модерацию...")
    caption = f"📝 Новый пост на проверку:\n\n{post_text}"
    return send_message(ADMIN_CHAT_ID, caption)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            print("[TEST] /test вызван")
            post_text = generate_post()
            if post_text:
                ok = send_for_approval(post_text)
                print(f"[TEST] Результат отправки: {ok}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"Пост сгенерирован и отправлен на модерацию: {ok}".encode())
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Ошибка генерации поста")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Сервер запущен на порту {port}")
    server.serve_forever()

threading.Thread(target=start_server, daemon=True).start()

print("Бот запущен (этап 2 с логированием)")
while True:
    import time
    time.sleep(60)