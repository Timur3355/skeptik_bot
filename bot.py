import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": ADMIN_CHAT_ID, "text": text}
        resp = requests.post(url, json=data, timeout=10)
        print(f"send_message status: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"send_message error: {e}")
        return False

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test':
            print("test вызван")
            ok = send_message("✅ Тестовое сообщение от /test")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"Сообщение отправлено: {ok}".encode())
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

print("Бот запущен")
while True:
    import time
    time.sleep(60)