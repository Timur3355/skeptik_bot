import os
import threading
import requests
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

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
    print("generate_post called")
    if not DEEPSEEK_API_KEY:
        print("No API key")
        return None
    url = "https://api.chatanywhere.tech/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Ты автор канала Скептик с EBITDA. Пиши дерзко, саркастично, с цифрами."},
            {"role": "user", "content": "Напиши пост про Магнит за 2023 с реальными цифрами."}
        ],
        "temperature": 0.85,
        "max_tokens": 400
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        print("API status:", resp.status_code)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        else:
            print("API error:", resp.text)
            return None
    except Exception as e:
        print("generate_post error:", e)
        return None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            print("/test called")
            post = generate_post()
            if post:
                send_message(ADMIN_CHAT_ID, f"Новый пост:\n{post}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Error")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print("Server started on port", port)
    server.serve_forever()

threading.Thread(target=start_server, daemon=True).start()
print("Bot started")
while True:
    time.sleep(60)