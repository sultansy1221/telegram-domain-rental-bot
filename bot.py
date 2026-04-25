import logging
import sqlite3
import os
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import threading

# --- الإعدادات ---
TOKEN = "8783824232:AAH4c9SK5pZM3NoBgoN6QkXD5Z_frxGqANg"
ADMIN_ID = 8395932049
DB_NAME = "bot_database.db"
BASE_URL = "https://your-app-name.onrender.com"  # سيتم تحديثه لاحقاً

# --- قاعدة البيانات ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE, username TEXT, balance REAL DEFAULT 0, is_admin INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rentals 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, subdomain TEXT UNIQUE, html_content TEXT, end_date TEXT, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    # إعدادات افتراضية
    c.execute("INSERT OR IGNORE INTO settings VALUES ('price_hour', '1.0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('price_day', '10.0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('price_month', '200.0')")
    
    # تعيين الأدمن
    c.execute("INSERT OR IGNORE INTO users (telegram_id, username, is_admin) VALUES (?, ?, ?)", (ADMIN_ID, 'Admin', 1))
    
    conn.commit()
    conn.close()

init_db()

# --- خادم الويب (Flask) ---
app = Flask(__name__)

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
            return render_template_string(html_content)
        else:
            return "<h1>هذا النطاق انتهت صلاحيته</h1>", 403
    return "<h1>النطاق غير موجود</h1>", 404

@app.route('/')
def index():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# --- بوت تليجرام ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("🌐 تأجير نطاق جديد", callback_data='rent_new')],
        [InlineKeyboardButton("👤 حسابي", callback_data='my_account')],
        [InlineKeyboardButton("💰 شحن الرصيد", callback_data='top_up')]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("مرحباً بك في بوت تأجير النطاقات! اختر من القائمة أدناه:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'rent_new':
        await query.edit_message_text("من فضلك أرسل اسم النطاق المطلوب (أحرف إنجليزية فقط):")
        context.user_data['state'] = 'awaiting_subdomain'
    
    elif query.data == 'my_account':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,))
        balance = c.fetchone()[0]
        c.execute("SELECT subdomain, end_date FROM rentals WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)", (user_id,))
        rentals = c.fetchall()
        conn.close()
        
        msg = f"👤 حسابك:\nالرصيد: {balance}$\n\nنطاقاتك:\n"
        for r in rentals:
            msg += f"- {r[0]} (ينتهي في: {r[1]})\n"
        await query.edit_message_text(msg)

    elif query.data == 'admin_panel' and user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("💵 تعديل الأسعار", callback_data='edit_prices')],
            [InlineKeyboardButton("📊 إحصائيات", callback_data='stats')]
        ]
        await query.edit_message_text("لوحة تحكم المسؤول:", reply_markup=InlineKeyboardMarkup(keyboard))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    text = update.message.text

    if state == 'awaiting_subdomain':
        # التحقق من توفر النطاق
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM rentals WHERE subdomain = ?", (text,))
        if c.fetchone():
            await update.message.reply_text("عذراً، هذا النطاق محجوز بالفعل. اختر اسماً آخر:")
        else:
            context.user_data['subdomain'] = text
            context.user_data['state'] = 'awaiting_html'
            await update.message.reply_text(f"النطاق {text} متاح! الآن أرسل كود HTML الذي تريد تشغيله:")
        conn.close()

    elif state == 'awaiting_html':
        context.user_data['html'] = text
        keyboard = [
            [InlineKeyboardButton("ساعة (1$)", callback_data='period_hour')],
            [InlineKeyboardButton("يوم (10$)", callback_data='period_day')],
            [InlineKeyboardButton("شهر (200$)", callback_data='period_month')]
        ]
        await update.message.reply_text("اختر مدة التأجير:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['state'] = 'awaiting_period'

async def period_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    period_type = query.data.split('_')[1]
    
    subdomain = context.user_data.get('subdomain')
    html = context.user_data.get('html')
    
    # حساب تاريخ الانتهاء
    now = datetime.now()
    if period_type == 'hour': end_date = now + timedelta(hours=1)
    elif period_type == 'day': end_date = now + timedelta(days=1)
    else: end_date = now + timedelta(days=30)
    
    # حفظ في قاعدة البيانات (تبسيط: الرصيد مجاني حالياً كما طلب المستخدم)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE telegram_id = ?", (user_id,))
    u_id = c.fetchone()[0]
    try:
        c.execute("INSERT INTO rentals (user_id, subdomain, html_content, end_date, status) VALUES (?, ?, ?, ?, ?)",
                  (u_id, subdomain, html, end_date.strftime('%Y-%m-%d %H:%M:%S'), 'active'))
        conn.commit()
        url = f"{BASE_URL}/s/{subdomain}"
        await query.edit_message_text(f"✅ تم تفعيل النطاق بنجاح!\nالرابط: {url}\nينتهي في: {end_date}")
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ: {str(e)}")
    conn.close()

if __name__ == '__main__':
    # تشغيل Flask في خيط منفصل
    threading.Thread(target=run_flask, daemon=True).start()
    
    # تشغيل البوت
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(button_handler, pattern='^(rent_new|my_account|top_up|admin_panel)$'))
    app_bot.add_handler(CallbackQueryHandler(period_selection, pattern='^period_'))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    
    print("Bot and Server are starting...")
    app_bot.run_polling()
