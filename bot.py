import os
import threading
import requests
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import traceback

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
API_PROVIDER = os.getenv("API_PROVIDER", "openai").lower()
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-ai/DeepSeek-V3")

if API_PROVIDER == "openai":
    API_URL = "https://api.chatanywhere.tech/v1/chat/completions"
elif API_PROVIDER == "siliconflow":
    API_URL = "https://api.siliconflow.cn/v1/chat/completions"
else:
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

print(f"API_PROVIDER: {API_PROVIDER}")
print(f"API_URL: {API_URL}")
print(f"MODEL_NAME: {MODEL_NAME}")
print(f"DEEPSEEK_API_KEY: {DEEPSEEK_API_KEY[:10] if DEEPSEEK_API_KEY else 'None'}...")

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        resp = requests.post(url, json=data, timeout=10)
        print("send_message status:", resp.status_code)
        return resp.status_code == 200
    except Exception as e:
        print("send_message error:", e)
        return False

def generate_post():
    print("[GEN] Starting generation...")
    if not DEEPSEEK_API_KEY:
        return None, "DEEPSEEK_API_KEY не задан в переменных окружения"
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        if API_PROVIDER == "openrouter":
            headers["HTTP-Referer"] = "https://skeptik-bot.onrender.com"
            headers["X-Title"] = "Скептик с EBITDA"

        payload = {
            "model": MODEL_NAME,
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
        print("[GEN] Sending request to", API_URL)
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        print("[GEN] API status:", resp.status_code)
        print("[GEN] API response:", resp.text[:500])
        if resp.status_code == 200:
            data = resp.json()
            post_text = data["choices"][0]["message"]["content"]
            print("[GEN] Post received, length:", len(post_text))
            return post_text, None
        else:
            error_msg = f"API вернул {resp.status_code}: {resp.text}"
            print("[GEN] Error:", error_msg)
            return None, error_msg
    except Exception as e:
        error_msg = f"Исключение: {e}\n{traceback.format_exc()}"
        print("[GEN] Exception:", error_msg)
        return None, error_msg

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            print("/test called")
            post_text, error = generate_post()
            if post_text:
                send_message(ADMIN_CHAT_ID, "✅ Пост сгенерирован! Проверь логи.")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"Пост сгенерирован, длина {len(post_text)}".encode())
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Ошибка генерации:\n{error}".encode())
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print("Server started on port:", port)
    server.serve_forever()

threading.Thread(target=start_server, daemon=True).start()
print("Bot is running")
while True:
    time.sleep(60)