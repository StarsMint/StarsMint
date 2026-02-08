import os
import threading
import time
import json
import logging
import sqlite3
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from flask import Flask
from database import init_db, get_strategy, create_strategy, delete_strategy
from okx_handler import get_server_status, check_market_conditions, execute_trade, check_open_orders_status, exchange

# إعدادات التوكن
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 1801208219))

# مراحل المحادثة
SET_PROFIT, SET_COINS = range(2)

# تهيئة قاعدة البيانات
init_db()

# إعداد السيرفر الوهمي (Render)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Alive"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# ----------------- منطق التداول (Background) -----------------
def trading_loop(application):
    while True:
        try:
            # تفقد الصفقات المغلقة
            close_msg = check_open_orders_status()
            if close_msg:
                 # إرسال رسالة باستخدام context البوت
                 application.bot.send_message(chat_id=ADMIN_ID, text=close_msg).result()

            # تفقد الاستراتيجية
            strategy = get_strategy()
            conn = sqlite3.connect("trading_bot.db")
            c = conn.cursor()
            c.execute("SELECT count(*) FROM trades WHERE status='OPEN'")
            open_trades_count = c.fetchone()[0]
            conn.close()

            if strategy and open_trades_count == 0:
                target_profit = strategy[1]
                coins = json.loads(strategy[2])
                
                for coin in coins:
                    symbol = coin.upper()
                    if "/" not in symbol: symbol += "/USDT"
                    
                    is_good_buy, rsi_val = check_market_conditions(symbol)
                    
                    if is_good_buy:
                        report = execute_trade(symbol, target_profit)
                        print(report) # يظهر في اللوج
                        break 
            
            time.sleep(20)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(20)

# ----------------- دوال التيليجرام -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return
    
    keyboard = [
        ["Create Strategy", "Status"],
        ["Trade Analysis", "Account Analysis"]
    ]
    await update.message.reply_text(
        "أهلاً بك يا زعيم 🚀\nالقائمة الرئيسية:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# --- نقطة دخول إنشاء الاستراتيجية ---
async def start_strategy_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return ConversationHandler.END

    strat = get_strategy()
    if strat:
        keyboard = [["Delete Strategy", "Cancel"]]
        await update.message.reply_text(
            f"⚠️ **تنبيه:** توجد استراتيجية نشطة!\n"
            f"🎯 الهدف: {strat[1]}%\n"
            f"💎 العملات: {strat[2]}\n\n"
            f"هل تريد حذفها لإنشاء جديدة؟",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return ConversationHandler.END # ننهي المحادثة هنا ونترك الزر "Delete" يتعامل معه الهاندلر العام
    else:
        await update.message.reply_text(
            "🛠 **إنشاء استراتيجية جديدة**\n\n"
            "أدخل نسبة الربح الصافي (بدون علامة %).\n"
            "مثال: اكتب `0.5` لربح نصف بالمائة.",
            parse_mode="Markdown",
             reply_markup=ReplyKeyboardMarkup([["Cancel"]], resize_keyboard=True)
        )
        return SET_PROFIT

async def set_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Cancel":
        await start(update, context)
        return ConversationHandler.END

    try:
        profit = float(text)
        context.user_data['profit'] = profit
        await update.message.reply_text(
            "✅ تم حفظ النسبة.\n\n"
            "الآن أرسل أسماء العملات مفصولة بفاصلة.\n"
            "مثال: `BTC, ETH, SOL`"
        )
        return SET_COINS
    except ValueError:
        await update.message.reply_text("❌ خطأ: الرجاء إدخال رقم صحيح (مثال: 0.5).")
        return SET_PROFIT

async def set_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Cancel":
        await start(update, context)
        return ConversationHandler.END

    coins = [c.strip().upper() + "/USDT" for c in text.split(',')]
    create_strategy(context.user_data['profit'], coins)
    
    await update.message.reply_text(
        f"✅ **تم تفعيل البوت بنجاح!**\n"
        f"سيقوم بمراقبة: {coins}\n"
        f"الهدف: {context.user_data['profit']}%",
        parse_mode="Markdown"
    )
    await start(update, context)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END

# --- الهاندلر العام للأزرار العادية ---
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return

    if text == "Status":
        ping, total, free = get_server_status()
        conn = sqlite3.connect("trading_bot.db")
        c = conn.cursor()
        c.execute("SELECT count(*), sum(profit_percent) FROM trades WHERE date(close_time) = date('now')")
        data = c.fetchone()
        count = data[0] if data else 0
        profit_today = data[1] if data and data[1] else 0.0
        conn.close()

        msg = (
            f"📊 **System Status**\n"
            f"📶 Ping: {ping}ms\n"
            f"💰 Wallet: {total:.2f} $\n"
            f"🆓 Free: {free:.2f} $\n"
            f"📅 Trades Today: {count}\n"
            f"📈 Profit Today: {profit_today:.2f}%"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "Delete Strategy":
        delete_strategy()
        await update.message.reply_text("🗑 تم حذف الاستراتيجية. يمكنك إنشاء واحدة جديدة الآن.")
        await start(update, context)

    elif text == "Cancel":
        await start(update, context)

    elif text == "Trade Analysis":
        conn = sqlite3.connect("trading_bot.db")
        c = conn.cursor()
        c.execute("SELECT symbol, entry_price, tp_price, profit_usdt FROM trades WHERE status='OPEN'")
        trade = c.fetchone()
        conn.close()
        
        if trade:
            symbol, entry, tp, _ = trade
            try:
                ticker = exchange.fetch_ticker(symbol)
                curr_price = ticker['last']
                # نسبة التقدم
                diff_total = tp - entry
                diff_current = curr_price - entry
                progress = (diff_current / diff_total) * 100
                
                await update.message.reply_text(
                    f"🔍 **Trade Analysis**\n"
                    f"Coin: {symbol}\n"
                    f"Entry: {entry}\n"
                    f"Current: {curr_price}\n"
                    f"Target: {tp}\n"
                    f"Progress: {progress:.2f}%"
                )
            except:
                await update.message.reply_text("⚠️ لا يمكن جلب السعر الحالي.")
        else:
            await update.message.reply_text("💤 لا توجد صفقات مفتوحة.")

    elif text == "Account Analysis":
        conn = sqlite3.connect("trading_bot.db")
        c = conn.cursor()
        c.execute("SELECT count(*), sum(profit_usdt) FROM trades WHERE status='CLOSED'")
        data = c.fetchone()
        conn.close()
        
        count = data[0] if data else 0
        profit = data[1] if data and data[1] else 0.0
        
        await update.message.reply_text(
            f"📜 **Account History**\n"
            f"Total Deals: {count}\n"
            f"Net Profit: {profit:.2f} USDT"
        )

# ----------------- التشغيل الرئيسي -----------------
if __name__ == '__main__':
    # Flask Thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Bot Setup
    app_bot = Application.builder().token(TOKEN).build()

    # 1. إعداد هاندلر المحادثة (الأهم، يجب أن يضاف أولاً)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Create Strategy$"), start_strategy_flow)],
        states={
            SET_PROFIT: [MessageHandler(filters.TEXT & ~filters.Regex("^(Cancel)$"), set_profit)],
            SET_COINS: [MessageHandler(filters.TEXT & ~filters.Regex("^(Cancel)$"), set_coins)],
        },
        fallbacks=[MessageHandler(filters.Regex("^Cancel$"), cancel_conversation)]
    )
    
    app_bot.add_handler(conv_handler)

    # 2. إعداد الأوامر الأساسية
    app_bot.add_handler(CommandHandler("start", start))

    # 3. إعداد هاندلر الأزرار العامة (يأتي أخيراً لكي لا يسرق التفاعل من المحادثة)
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))

    # Trading Loop Thread
    # ملاحظة: تمرير app_bot للثريد عشان نقدر نرسل تنبيهات
    trading_thread = threading.Thread(target=trading_loop, args=(app_bot,))
    trading_thread.start()

    print("Bot Started Successfully...")
    app_bot.run_polling()
