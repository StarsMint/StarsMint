import os
import time
import random
import threading
import requests
import ccxt
import pandas as pd
import pandas_ta as ta
from flask import Flask

# ==========================================
# 1. إعدادات السيرفر الوهمي (Keep Alive) ☕
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "I'm alive! The Hunter Bot is running."

def run_http():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run_http)
    t.start()

# ==========================================
# 2. إعدادات التيليجرام (Telegram Handler) 📢
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
# أو حط التوكن هنا مباشرة بين علامات تنصيص اذا ما زبطت البيئة
# TELEGRAM_TOKEN = 'YOUR_BOT_TOKEN_HERE' 

TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") # اختياري، يمكن جلبه من التحديثات
# لمعرفة Chat ID، ارسل رسالة للبوت ثم افتح: https://api.telegram.org/bot<TOKEN>/getUpdates

def send_telegram_msg(message):
    try:
        if not TELEGRAM_TOKEN:
            print("⚠️ Telegram Token not found!")
            return
            
        # إذا لم يكن لدينا Chat ID، نحاول جلبه من آخر تحديث (طريقة بدائية لكن فعالة)
        global TELEGRAM_CHAT_ID
        if not TELEGRAM_CHAT_ID:
            updates_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            resp = requests.get(updates_url).json()
            if resp['result']:
                TELEGRAM_CHAT_ID = resp['result'][0]['message']['chat']['id']
            else:
                print("⚠️ Send a message to the bot first to get Chat ID.")
                return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, data=data)
    except Exception as e:
        print(f"Error sending telegram msg: {e}")

# ==========================================
# 3. محرك OKX (The Engine) ⚙️
# ==========================================
# إعدادات الاتصال
api_key = os.environ.get('OKX_API_KEY')
secret_key = os.environ.get('OKX_SECRET_KEY')
password = os.environ.get('OKX_PASSWORD')

# تأكد من وضع المفاتيح هنا إذا لم تستخدم Environment Variables
# api_key = '...'
# secret_key = '...'
# password = '...'

exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret_key,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}  # فيوتشرز
})

def get_top_volatile_coins(limit=30):
    """جلب أكثر العملات تقلباً وسيولة"""
    try:
        tickers = exchange.fetch_tickers()
        valid_tickers = [
            symbol for symbol in tickers 
            if '/USDT:USDT' in symbol 
        ]
        # الترتيب حسب الفوليوم
        sorted_tickers = sorted(valid_tickers, key=lambda x: tickers[x]['quoteVolume'], reverse=True)
        return sorted_tickers[:limit]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def analyze_market(symbol):
    """تحليل فني: RSI + Bollinger Bands"""
    try:
        # فريم 5 دقائق للسرعة
        bars = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        if not bars: return False, 0
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # المؤشرات
        bb = ta.bbands(df['close'], length=20, std=2)
        df = pd.concat([df, bb], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last_close = df['close'].iloc[-1]
        try:
            last_lower_bb = df['BBL_20_2.0'].iloc[-1]
        exceptKeyError:
             # أحياناً تتغير أسماء الأعمدة في pandas_ta
            last_lower_bb = df[df.columns[6]].iloc[-1] # محاولة الوصول بالترتيب

        last_rsi = df['rsi'].iloc[-1]
        
        # === شروط الدخول ===
        # السعر تحت البولنجر السفلي + RSI تحت 30
        is_buy_signal = (last_close < last_lower_bb) and (last_rsi < 30)
        
        return is_buy_signal, last_close
        
    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return False, 0

def execute_futures_trade(symbol, leverage=10):
    """تنفيذ الصفقة All-in"""
    try:
        # 1. الرافعة
        try:
            exchange.set_leverage(leverage, symbol)
            exchange.set_margin_mode('isolated', symbol)
        except: pass

        # 2. الرصيد
        balance = exchange.fetch_balance()
        usdt_balance = balance['free']['USDT']
        
        if usdt_balance < 2: return "LOW_BALANCE"

        # 3. الكمية
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        amount = (usdt_balance * leverage * 0.95) / price
        amount = exchange.amount_to_precision(symbol, amount)
        
        # 4. شراء Market
        order = exchange.create_market_buy_order(symbol, amount)
        entry_price = float(order['average']) if order['average'] else price
        
        # 5. TP / SL
        tp_price = entry_price * 1.015 # هدف 1.5% (15% مع الرافعة)
        sl_price = entry_price * 0.99  # وقف 1% (10% مع الرافعة)
        
        try:
            exchange.create_order(symbol, 'limit', 'sell', amount, tp_price, params={'reduceOnly': True})
            exchange.create_order(symbol, 'stop', 'sell', amount, sl_price, params={'stopPrice': sl_price, 'reduceOnly': True})
        except Exception as e:
            return f"⚠️ تم الشراء {symbol} لكن فشل TP/SL: {e}"

        return f"🚀 تم القنص: {symbol}\nالدخول: {entry_price}\nالهدف: {tp_price}"

    except Exception as e:
        return f"❌ فشل التنفيذ: {e}"

def check_open_positions():
    """مراقبة الصفقات المفتوحة"""
    try:
        positions = exchange.fetch_positions()
        count = 0
        for pos in positions:
            if float(pos['contracts']) > 0:
                count += 1
        return count
    except:
        return 0

# ==========================================
# 4. التشغيل الرئيسي (Main Loop) 🏁
# ==========================================
if __name__ == "__main__":
    # تشغيل السيرفر لعدم النوم
    keep_alive()
    
    print("🤖 Bot started...")
    send_telegram_msg("🔥 تم تشغيل البوت (The Hunter) بنجاح!")
    
    while True:
        try:
            # التحقق من الصفقات المفتوحة
            if check_open_positions() > 0:
                print("يوجد صفقة مفتوحة، الانتظار...")
                time.sleep(60)
                continue
            
            # المسح الضوئي
            print("🔎 Scan market...")
            coins = get_top_volatile_coins(limit=25)
            random.shuffle(coins)
            
            found = False
            for coin in coins:
                print(f"Checking {coin}...")
                is_buy, price = analyze_market(coin)
                
                if is_buy:
                    msg = f"⚡️ فرصة على {coin} بسعر {price}"
                    print(msg)
                    send_telegram_msg(msg)
                    
                    res = execute_futures_trade(coin, leverage=10)
                    send_telegram_msg(res)
                    
                    found = True
                    break # صفقة واحدة تكفي
                
                time.sleep(1.5) # تجنب الحظر
            
            if not found:
                print("No opportunities. Sleeping 30s...")
                time.sleep(30)
                
        except Exception as e:
            print(f"Critical Error: {e}")
            time.sleep(10)
