import telebot
from telebot import types
import sqlite3
import time
import threading
import requests
import datetime
import json # ضرورية لقراءة ردود المواقع الحديثة
from bip_utils import Bip39MnemonicGenerator, Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

# --- CONFIGURATION ---
TOKEN = "8534128722:AAFvlBEXHK-2KP-ZXjCJclSWcg1xFf5iQDk"
ADMIN_ID = 1801208219

# Rate limiting (Seconds to wait between checks per thread)
# بما أننا نستخدم 3 مواقع، يمكننا تقليل الانتظار قليلاً لزيادة السرعة بأمان
RATE_LIMIT_DELAY = 2.3

bot = telebot.TeleBot(TOKEN)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    c = conn.cursor()
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    subscription_end TIMESTAMP,
                    is_active INTEGER DEFAULT 0,
                    active_words INTEGER DEFAULT 0
                )''')
    # Payments history
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    date TIMESTAMP
                )''')
    conn.commit()
    conn.close()

init_db()

# --- DATABASE HELPERS ---
def get_db_connection():
    return sqlite3.connect('bot_database.db', check_same_thread=False)

def is_user_subscribed(user_id):
    if user_id == ADMIN_ID:
        return True
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT subscription_end FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    if result and result[0]:
        sub_end = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S.%f')
        if sub_end > datetime.datetime.now():
            return True
    return False

def add_subscription(user_id, username, days=7):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT subscription_end FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    
    now = datetime.datetime.now()
    if result and result[0]:
        current_end = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S.%f')
        if current_end > now:
            new_end = current_end + datetime.timedelta(days=days)
        else:
            new_end = now + datetime.timedelta(days=days)
    else:
        new_end = now + datetime.timedelta(days=days)
        
    c.execute("INSERT OR REPLACE INTO users (user_id, username, subscription_end, is_active) VALUES (?, ?, ?, ?)",
              (user_id, username, new_end, 0)) 
    conn.commit()
    conn.close()
    return new_end

# --- BITCOIN & SCANNING LOGIC ---
stop_flags = {} 

def generate_wallet(words_count=12):
    # Generate Mnemonic
    mnemonic = Bip39MnemonicGenerator().FromWordsNumber(words_count)
    # Generate Seed
    seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
    # Generate BTC Address (Legacy P2PKH)
    bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.BITCOIN)
    bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0)
    address = bip44_acc_ctx.PublicKey().ToAddress()
    
    return str(mnemonic), address

# --- MULTI-API HANDLERS ---
# دوال خاصة لكل موقع لأن طريقة الرد تختلف بينهم

def get_balance_mempool(address):
    # Mempool.space API (Returns JSON)
    url = f"https://mempool.space/api/address/{address}"
    r = requests.get(url, timeout=5)
    if r.status_code == 200:
        data = r.json()
        # الرصيد = الوارد - المصروف
        funded = data['chain_stats']['funded_txo_sum']
        spent = data['chain_stats']['spent_txo_sum']
        return funded - spent
    raise Exception(f"Status {r.status_code}")

def get_balance_blockchain_info(address):
    # Blockchain.info API (Returns Text)
    url = f"https://blockchain.info/q/addressbalance/{address}"
    r = requests.get(url, timeout=5)
    if r.status_code == 200:
        return int(r.text)
    raise Exception(f"Status {r.status_code}")

def get_balance_blockstream(address):
    # Blockstream API (Returns JSON)
    url = f"https://blockstream.info/api/address/{address}"
    r = requests.get(url, timeout=5)
    if r.status_code == 200:
        data = r.json()
        funded = data['chain_stats']['funded_txo_sum']
        spent = data['chain_stats']['spent_txo_sum']
        return funded - spent
    raise Exception(f"Status {r.status_code}")

def check_balance_multi_source(address):
    """
    نظام التدوير الذكي:
    يحاول الموقع الأول -> فشل؟ -> الموقع الثاني -> فشل؟ -> الموقع الثالث.
    لن يخرج إلا بنتيجة حقيقية.
    """
    # ترتيب المواقع حسب الأفضلية والسرعة
    providers = [
        get_balance_mempool,       # الأفضل والأسرع
        get_balance_blockchain_info, # الكلاسيكي
        get_balance_blockstream    # الاحتياطي
    ]
    
    while True:
        # محاولة كل مزود خدمة بالترتيب
        for provider in providers:
            try:
                balance = provider(address)
                return balance # تم الحصول على الرصيد بنجاح!
            except Exception:
                # إذا فشل مزود (حظر أو بطء)، ننتقل فوراً للتالي دون انتظار
                continue
        
        # إذا فشلت كل المواقع الـ 3 (مشكلة انترنت عامة أو حظر شامل)
        print("⚠️ All APIs are busy/down. Cooling down for 5s...")
        time.sleep(5) 
        # تعاد الحلقة من جديد لنفس المحفظة

def scanning_task(user_id, words_count):
    conn = get_db_connection()
    c = conn.cursor()
    
    while user_id in stop_flags and stop_flags[user_id]:
        if not is_user_subscribed(user_id):
            bot.send_message(user_id, "⚠️ **Subscription Expired.** Process stopped.", parse_mode="Markdown")
            stop_flags[user_id] = False
            break

        try:
            # 1. Generate
            seed_phrase, address = generate_wallet(words_count)
            
            # 2. Check Balance (Multi-Source Rotation)
            # نستخدم الدالة الجديدة هنا
            balance_sats = check_balance_multi_source(address)
            balance_btc = balance_sats / 100000000
            
            # 3. Message
            msg_text = (
                f"🔑 **Seed Phrase ({words_count} words):**\n`{seed_phrase}`\n\n"
                f"👛 **Address:** `{address}`\n"
                f"💰 **Balance:** {balance_btc} BTC"
            )
            
            # 4. Notify
            if balance_btc >= 0.00001:
                sent_msg = bot.send_message(user_id, "🚨 **BALANCE FOUND!** 🚨\n\n" + msg_text, parse_mode="Markdown")
                try:
                    bot.pin_chat_message(user_id, sent_msg.message_id)
                except:
                    pass
            else:
                # نرسل رسالة الفحص العادية (يتم التحكم بالسرعة لتجنب حظر التيليجرام)
                try:
                    bot.send_message(user_id, msg_text, parse_mode="Markdown")
                except Exception:
                    time.sleep(2) 

            # 5. Rate Limit
            time.sleep(RATE_LIMIT_DELAY)
            
        except Exception as e:
            print(f"Error in thread loop: {e}")
            time.sleep(5)

    c.execute("UPDATE users SET is_active=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# --- BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

    if is_user_subscribed(user_id):
        show_main_menu(message)
    else:
        markup = types.InlineKeyboardMarkup()
        pay_btn = types.InlineKeyboardButton("⭐️ Pay 100 Stars (7 Days)", pay=True)
        markup.add(pay_btn)
        
        intro_text = (
            "Welcome! This bot generates Bitcoin wallets and checks for balances automatically.\n\n"
            "**System:** Multi-API Rotation (Anti-Ban & High Speed)\n"
            "**How it works:**\n"
            "1. Generates 12/18/24 word seed phrases.\n"
            "2. Checks Blockchain using 3 different sources.\n"
            "3. Notifies you immediately if funds are found!\n\n"
            "**Price:** 100 Stars for 7 Days access.\n"
            "Click the button below to subscribe."
        )
        bot.send_invoice(
            message.chat.id,
            title="Bot Access (7 Days)",
            description="Unlimited Bitcoin wallet scanning for 1 week.",
            invoice_payload="7_days_sub",
            provider_token="", 
            currency="XTR",
            prices=[types.LabeledPrice("Subscription", 100)],
            reply_markup=markup
        )
        bot.send_message(message.chat.id, intro_text, parse_mode="Markdown")

def show_main_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("12 Words", "18 Words", "24 Words")
    markup.row("🔴 Stop / Terminate")
    
    if message.from_user.id == ADMIN_ID:
        status_text = "Welcome Admin. You have unlimited controls."
    else:
        status_text = "Subscription Active. Select scan mode to start."
        
    bot.send_message(message.chat.id, status_text, reply_markup=markup)

# --- PAYMENT ---
@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    user_id = message.from_user.id
    username = message.from_user.username
    amount = message.successful_payment.total_amount
    
    new_end_date = add_subscription(user_id, username)
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO payments (user_id, amount, date) VALUES (?, ?, ?)", 
              (user_id, amount, datetime.datetime.now()))
    conn.commit()
    conn.close()
    
    bot.send_message(user_id, f"✅ Payment successful! Expires: {new_end_date}\nType /start.")
    bot.send_message(ADMIN_ID, f"💰 **New Sale!**\nUser: @{username}\nAmount: {amount}")

# --- CONTROLS ---

@bot.message_handler(func=lambda message: message.text in ["12 Words", "18 Words", "24 Words"])
def start_scan(message):
    user_id = message.from_user.id
    
    if not is_user_subscribed(user_id):
        bot.send_message(user_id, "Subscription expired. /start to renew.")
        return

    words_map = {"12 Words": 12, "18 Words": 18, "24 Words": 24}
    count = words_map[message.text]

    is_running = user_id in stop_flags and stop_flags[user_id]
    
    if user_id != ADMIN_ID:
        if is_running:
            bot.send_message(user_id, "⚠️ Scan already running. Stop it first.")
            return
        
        stop_flags[user_id] = True
        t = threading.Thread(target=scanning_task, args=(user_id, count))
        t.daemon = True
        t.start()
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE users SET is_active=1, active_words=? WHERE user_id=?", (count, user_id))
        conn.commit()
        conn.close()
        bot.send_message(user_id, f"🚀 **Started scanning {count} words!**", parse_mode="Markdown")
        
    else:
        if is_running:
             bot.send_message(user_id, "Admin: Restarting process...")
             stop_flags[user_id] = False
             time.sleep(2)
             
        stop_flags[user_id] = True
        t = threading.Thread(target=scanning_task, args=(user_id, count))
        t.daemon = True
        t.start()
        bot.send_message(user_id, f"🚀 **Admin Scan Started ({count} words)**", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "🔴 Stop / Terminate")
def stop_scan(message):
    user_id = message.from_user.id
    if user_id in stop_flags and stop_flags[user_id]:
        stop_flags[user_id] = False
        bot.send_message(user_id, "🛑 Stopping process... Please wait.")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE users SET is_active=0 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
    else:
        bot.send_message(user_id, "No active process found.")

@bot.message_handler(commands=['reload'])
def reload_bot(message):
    bot.send_message(message.chat.id, "♻️ System Reloaded.")

def notify_on_restart():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_active=1")
    users = c.fetchall()
    conn.close()
    for u in users:
        try:
            bot.send_message(u[0], "⚠️ **Server Restarted.**\nPlease type /reload to resume.", parse_mode="Markdown")
        except:
            pass

if __name__ == "__main__":
    print("Bot started with Multi-API rotation...")
    notify_on_restart()
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except (Exception, KeyboardInterrupt) as e:
        print(f"Bot Error: {e}")
