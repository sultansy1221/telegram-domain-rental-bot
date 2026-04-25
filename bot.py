import logging
import sqlite3
import os
from datetime import datetime, timedelta
from flask import Flask, request, make_response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import threading
import io

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
    c.execute('''CREATE TABLE IF NOT EXISTS rentals 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, subdomain TEXT UNIQUE, html_content TEXT, end_date TEXT, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    c.execute("INSERT OR IGNORE INTO settings VALUES ('price_hour', '1.0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('price_day', '10.0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('price_month', '200.0')")
    
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
            response = make_response(html_content)
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            return response
        else:
            return "<h1>عذراً، انتهت صلاحية هذا النطاق</h1>", 403
    return "<h1>النطاق غير موجود</h1>", 404

@app.route('/')
def index():
    return "<h1>Bot is active and serving domains!</h1>"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# --- بوت تليجرام ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("🌐 تأجير نطاق جديد", callback_data='rent_new')],
        [InlineKeyboardButton("👤 حسابي", callback_data='my_account')],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم (أدمن)", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"مرحباً {username}! في بوت تأجير النطاقات.\nيمكنك الآن إرسال كود HTML كنص أو رفع ملف .html مباشرة.", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'rent_new':
        await query.edit_message_text("أرسل اسم النطاق المطلوب (أحرف إنجليزية فقط):")
        context.user_data['state'] = 'awaiting_subdomain'
    
    elif query.data == 'my_account':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,))
        balance = c.fetchone()[0]
        c.execute("SELECT subdomain, end_date FROM rentals WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)", (user_id,))
        rentals = c.fetchall()
        conn.close()
        
        msg = f"👤 حسابك:\n💰 الرصيد الحالي: {balance}$\n\nنطاقاتك النشطة:\n"
        if not rentals:
            msg += "لا يوجد لديك نطاقات حالياً."
        for r in rentals:
            msg += f"- {BASE_URL}/s/{r[0]} (ينتهي: {r[1]})\n"
        await query.edit_message_text(msg)

    elif query.data == 'admin_panel' and user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("➕ إضافة رصيد لمستخدم", callback_data='admin_add_balance')],
            [InlineKeyboardButton("📊 إحصائيات عامة", callback_data='admin_stats')]
        ]
        await query.edit_message_text("لوحة تحكم المسؤول:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'admin_add_balance' and user_id == ADMIN_ID:
        await query.edit_message_text("أرسل ID المستخدم متبوعاً بالمبلغ، مثال:\n`12345678 50`")
        context.user_data['state'] = 'admin_adding_balance'

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == 'awaiting_html':
        doc = update.message.document
        if doc.file_name.endswith('.html') or doc.file_name.endswith('.txt'):
            file = await context.bot.get_file(doc.file_id)
            content = await file.download_as_bytearray()
            html_text = content.decode('utf-8')
            
            context.user_data['html'] = html_text
            keyboard = [
                [InlineKeyboardButton("ساعة (1$)", callback_data='period_hour')],
                [InlineKeyboardButton("يوم (10$)", callback_data='period_day')],
                [InlineKeyboardButton("شهر (200$)", callback_data='period_month')]
            ]
            await update.message.reply_text("✅ تم استلام الملف بنجاح! اختر مدة التأجير:", reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['state'] = 'awaiting_period'
        else:
            await update.message.reply_text("❌ عذراً، يجب أن يكون الملف بصيغة .html أو .txt")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    text = update.message.text

    if state == 'admin_adding_balance' and user_id == ADMIN_ID:
        try:
            target_id, amount = text.split()
            amount = float(amount)
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, target_id))
            if c.rowcount > 0:
                conn.commit()
                await update.message.reply_text(f"✅ تم إضافة {amount}$ لحساب المستخدم {target_id}")
                try:
                    await context.bot.send_message(chat_id=int(target_id), text=f"💰 تم إضافة {amount}$ إلى رصيدك من قبل الإدارة!")
                except: pass
            else:
                await update.message.reply_text("❌ لم يتم العثور على المستخدم.")
            conn.close()
        except:
            await update.message.reply_text("❌ خطأ في التنسيق. أرسل: ID المبلغ")
        context.user_data['state'] = None

    elif state == 'awaiting_subdomain':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM rentals WHERE subdomain = ?", (text,))
        if c.fetchone():
            await update.message.reply_text("❌ هذا النطاق محجوز. اختر اسماً آخر:")
        else:
            context.user_data['subdomain'] = text
            context.user_data['state'] = 'awaiting_html'
            await update.message.reply_text(f"✅ النطاق {text} متاح!\nالآن أرسل كود HTML كنص أو قم برفع ملف .html:")
        conn.close()

    elif state == 'awaiting_html':
        context.user_data['html'] = text
        keyboard = [
            [InlineKeyboardButton("ساعة (1$)", callback_data='period_hour')],
            [InlineKeyboardButton("يوم (10$)", callback_data='period_day')],
            [InlineKeyboardButton("شهر (200$)", callback_data='period_month')]
        ]
        await update.message.reply_text("✅ تم استلام الكود! اختر مدة التأجير:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['state'] = 'awaiting_period'

async def period_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    period_type = query.data.split('_')[1]
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (f'price_{period_type}',))
    price = float(c.fetchone()[0])
    
    c.execute("SELECT balance, id FROM users WHERE telegram_id = ?", (user_id,))
    user_data = c.fetchone()
    balance, u_id = user_data[0], user_data[1]
    
    if balance < price:
        await query.edit_message_text(f"❌ رصيدك غير كافٍ. السعر: {price}$ ورصيدك: {balance}$\nتواصل مع الإدارة لشحن الرصيد.")
        conn.close()
        return

    subdomain = context.user_data.get('subdomain')
    html = context.user_data.get('html')
    
    now = datetime.now()
    if period_type == 'hour': end_date = now + timedelta(hours=1)
    elif period_type == 'day': end_date = now + timedelta(days=1)
    else: end_date = now + timedelta(days=30)
    
    try:
        c.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (price, u_id))
        c.execute("INSERT INTO rentals (user_id, subdomain, html_content, end_date, status) VALUES (?, ?, ?, ?, ?)",
                  (u_id, subdomain, html, end_date.strftime('%Y-%m-%d %H:%M:%S'), 'active'))
        conn.commit()
        url = f"{BASE_URL}/s/{subdomain}"
        await query.edit_message_text(f"✅ تم التفعيل بنجاح!\n🔗 الرابط: {url}\n📅 ينتهي في: {end_date.strftime('%Y-%m-%d %H:%M')}")
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ: {str(e)}")
    conn.close()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(button_handler, pattern='^(rent_new|my_account|admin_panel|admin_add_balance)$'))
    app_bot.add_handler(CallbackQueryHandler(period_selection, pattern='^period_'))
    app_bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    
    print("Bot is running...")
    app_bot.run_polling()
