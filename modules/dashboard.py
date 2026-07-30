from flask import Flask, render_template, request
import sqlite3

app = Flask(__name__)

@app.route('/')
def index():
    conn = sqlite3.connect('posts.db')
    cur = conn.cursor()
    cur.execute("SELECT text, rating, views, reactions FROM posts ORDER BY id DESC LIMIT 10")
    posts = cur.fetchall()
    return render_template('dashboard.html', posts=posts)

# Запуск: threading.Thread(target=app.run, kwargs={'host':'0.0.0.0', 'port':5000}).start()