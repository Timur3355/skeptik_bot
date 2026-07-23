import os
import threading
import requests
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

print(f"TELEGRAM_BOT_TOKEN = {TELEGRAM_BOT_TOKEN[:10] if TELEGRAM_BOT_TOKEN else 'None'}...")
print(f"ADMIN_CHAT_ID = {ADMIN_CHAT_ID}")
print(f"DEEPSEEK_API_KEY = {DEEPSEEK_API_KEY[:10] if DEEPSEEK_API_KEY else 'None'}...")

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        resp = requests.post(url, json=data, timeout=10)
        print(f"send_message status: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"send_message error: {e}")
        return False

def generate_post():
    print("[GEN] Начало генерации...")
    if not DEEPSEEK_API_KEY:
        print("[GEN] ОШИБКА: DEEPSEEK_API_KEY не задан")
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
                    "content": "Ты — автор канала «Скептик с EBITDA». Стиль: дерзкий, саркастичный, с реальными цифрами. Пост 3-4 абзаца, 400-600 символов. Используй эмодзи."
                },
                {
                    "role": "user",
                    "content": "Напиши пост про ошибки Магнита за 2023 год с реальными цифрами."
                }
            ],
            "temperature": 0.85,
            "max_tokens": 400
        }
        print("[GEN] Отправка запроса к OpenRouter...")
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        print(f"[GEN] Статус API: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            post_text = data["choices"][0]["message"]["content"]
            print(f"[GEN] Пост получен, длина {len(post_text)}")
            print(f"[GEN] Текст поста:\n{post_text[:200]}...")
            return post_text
        else:
            print(f"[GEN] Ошибка API: {resp.text}")
            return None
    except Exception as e:
        print(f"[GEN] Исключение: {e}")
        return None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            print("/test вызван")
            post_text = generate_post()
            if post_text:
                # Отправляем только подтверждение в Telegram
                send_message(ADMIN_CHAT_ID, "✅ Генерация поста прошла успешно! Смотри логи.")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Пост сгенерирован, проверьте логи")
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Ошибка генерации")
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
print("Бот работает (тест генерации)")
while True:
    time.sleep(60)