import logging
import sqlite3
import os
from datetime import datetime
from flask import Flask, request, make_response, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import threading

# --- الإعدادات ---
TOKEN = "8783824232:AAH4c9SK5pZM3NoBgoN6QkXD5Z_frxGqANg"
ADMIN_ID = 8395932049
DB_NAME = "bot_database.db"

# محاولة جلب الرابط تلقائياً من Render أو استخدام متغير البيئة
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
BASE_URL = os.environ.get('BASE_URL', RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else 'https://your-app-name.onrender.com')

# --- قاعدة البيانات ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE, username TEXT, balance REAL DEFAULT 0, is_admin INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, details TEXT, status TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rentals 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, subdomain TEXT UNIQUE, html_content TEXT, end_date TEXT, status TEXT)''')
    
    c.execute("INSERT OR IGNORE INTO users (telegram_id, username, is_admin) VALUES (?, ?, ?)", (ADMIN_ID, 'Admin', 1))
    conn.commit()
    conn.close()

init_db()

# --- خادم الويب (Flask) ---
app = Flask(__name__)

# نقطة نهاية لاستقبال الطلبات من ملف HTML (Webhook)
@app.route('/api/order', methods=['POST'])
def receive_order():
    data = request.json
    if not data:
        return jsonify({"error": "No data received"}), 400
    
    # استخراج البيانات من الطلب
    order_id = data.get('order_id', 'N/A')
    amount = data.get('amount', '0')
    details = data.get('details', 'No details')
    user_info = data.get('user_info', 'Unknown User')
    
    # إرسال إشعار للأدمن مع أزرار
    message_text = (
        f"🔔 **طلب جديد وارد!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 رقم الطلب: `{order_id}`\n"
        f"👤 المستخدم: {user_info}\n"
        f"💰 المبلغ: {amount}\n"
        f"📝 التفاصيل: {details}\n"
        f"⏰ الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{order_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{order_id}")
        ]
    ]
    
    # سنستخدم خيط منفصل لإرسال الرسالة لتجنب تأخير الرد على الـ API
    def notify_admin():
        import requests
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {
            "chat_id": ADMIN_ID,
            "text": message_text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": keyboard}
        }
        requests.post(url, json=payload)

    threading.Thread(target=notify_admin).start()
    
    return jsonify({"status": "success", "message": "Order received and admin notified"}), 200

@app.route('/s/<subdomain>')
def serve_subdomain(subdomain):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT html_content, end_date FROM rentals WHERE subdomain = ? AND status = 'active'", (subdomain,))
    result = c.fetchone()
    conn.close()
    
    if result:
        html_content, end_date = result
        if datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S') > datetime.now():
            response = make_response(html_content)
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            return response
        else:
            return "<h1>عذراً، انتهت صلاحية هذا النطاق</h1>", 403
    return "<h1>النطاق غير موجود</h1>", 404

@app.route('/')
def index():
    return "<h1>Bot Monitor is Active!</h1>"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# --- بوت تليجرام ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        await update.message.reply_text("مرحباً أيها المسؤول. أنا الآن أراقب الطلبات الواردة من ملف HTML الخاص بك.")
    else:
        await update.message.reply_text("مرحباً بك في بوت المراقبة. سيتم إرسال طلباتك للمسؤول للمراجعة.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    action, order_id = data.split('_')
    
    if action == "approve":
        status_text = "✅ تم الموافقة على الطلب بنجاح."
        # هنا يمكنك إضافة كود لإرسال رسالة للمستخدم أو تحديث قاعدة البيانات
    else:
        status_text = "❌ تم رفض الطلب."
    
    await query.edit_message_text(text=f"{query.message.text}\n\n━━━━━━━━━━━━━━\n{status_text}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ميزة رفع الملفات السابقة (للتأجير)
    user_id = update.effective_user.id
    doc = update.message.document
    if doc.file_name.lower().endswith(('.html', '.htm')):
        file = await context.bot.get_file(doc.file_id)
        file_content = await file.download_as_bytearray()
        html_text = file_content.decode('utf-8', errors='ignore')
        
        # تخزين الملف كـ "نطاق فرعي" للمستخدم (لأغراض العرض)
        subdomain = f"user_{user_id}_{int(datetime.now().timestamp())}"
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        end_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT INTO rentals (user_id, subdomain, html_content, end_date, status) VALUES (?, ?, ?, ?, ?)",
                  (user_id, subdomain, html_text, end_date, 'active'))
        conn.commit()
        conn.close()
        
        url = f"{BASE_URL}/s/{subdomain}"
        await update.message.reply_text(f"✅ تم رفع الملف وتشغيله!\n🔗 الرابط: {url}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(callback_handler))
    app_bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("Bot Monitor is running...")
    app_bot.run_polling()
