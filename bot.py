import requests
import time
import schedule
from datetime import datetime
import os
import threading
import urllib.request

# ========== ВСЕ КЛЮЧИ БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# ==============================================================

def generate_post():
    """Запрос к DeepSeek: генерирует текст поста и промпт для картинки"""
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — автор канала «Скептик с EBITDA». Твой стиль: дерзкий, саркастичный, "
                    "с конкретными цифрами из отчётов Ozon, Wildberries или Магнита. "
                    "В конце каждого поста — чёткий Action Item для читателя. "
                    "Отделяй описание для картинки тремя знаками равно: ===\n\n"
                    "Формат ответа:\n"
                    "ТЕКСТ ПОСТА\n"
                    "===\n"
                    "ОПИСАНИЕ ДЛЯ КАРТИНКИ (на английском, кратко)"
                )
            },
            {
                "role": "user",
                "content": "Напиши пост на свежую тему про ошибки российских ритейлеров. Используй реальные цифры из отчётности."
            }
        ],
        "temperature": 0.9,
        "max_tokens": 1000
    }
    response = requests.post(url, headers=headers, json=payload)
    data = response.json()
    full_text = data["choices"][0]["message"]["content"]
    
    if "===" in full_text:
        post_text, image_prompt = full_text.split("===", 1)
    else:
        post_text = full_text
        image_prompt = "business finance sarcastic illustration"
    
    return post_text.strip(), image_prompt.strip()

def generate_image(prompt):
    """Генерация картинки через Pollinations.ai (бесплатно)"""
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=1200&height=800"
    response = requests.get(url)
    if response.status_code == 200:
        with open("temp_image.jpg", "wb") as f:
            f.write(response.content)
        return "temp_image.jpg"
    return None

def publish_to_telegram(text, image_path):
    """Публикация поста в канал"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(image_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": text}
        response = requests.post(url, files=files, data=data)
    return response.status_code == 200

def job():
    """Главная задача – генерация и публикация"""
    print(f"[{datetime.now()}] Генерация поста...")
    post_text, image_prompt = generate_post()
    print(f"[{datetime.now()}] Текст получен, промпт: {image_prompt[:50]}...")
    image_path = generate_image(image_prompt)
    if not image_path:
        print(f"[{datetime.now()}] ОШИБКА: не удалось сгенерировать картинку")
        return
    success = publish_to_telegram(post_text, image_path)
    if success:
        print(f"[{datetime.now()}] ✅ Пост опубликован!")
    else:
        print(f"[{datetime.now()}] ❌ Ошибка публикации")

# ========== САМОПИНГ (чтобы бот не засыпал на Render) ==========
def keep_alive():
    url = "https://ВАШ_САЙТ.onrender.com"  # ЗАМЕНИТЕ ПОСЛЕ ДЕПЛОЯ
    while True:
        try:
            urllib.request.urlopen(url)
            print("[keep-alive] Пинг успешен")
        except:
            print("[keep-alive] Ошибка пинга")
        time.sleep(600)  # каждые 10 минут

threading.Thread(target=keep_alive, daemon=True).start()
# ================================================================

# ========== РАСПИСАНИЕ ==========
schedule.every().day.at("10:00").do(job)

print("Бот запущен. Ожидание расписания...")

while True:
    schedule.run_pending()
    time.sleep(60)