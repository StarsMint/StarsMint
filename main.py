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
TELEGRAM_TOKEN = "8053838829:AAHo1iTJm958nIBgOoinGZpwTdm467lCBT4"

TELEGRAM_CHAT_ID = 1801208219 

def send_telegram_msg(message):
    try:
        if not TELEGRAM_TOKEN:
            print("⚠️ Telegram Token not found!")
            return
            
        global TELEGRAM_CHAT_ID
        if not TELEGRAM_CHAT_ID:
            updates_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            try:
                resp = requests.get(updates_url).json()
                if resp['result']:
                    TELEGRAM_CHAT_ID = resp['result'][0]['message']['chat']['id']
                else:
                    print("⚠️ Send a message to the bot first to get Chat ID.")
                    return
            except:
                pass

        if TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
            requests.post(url, data=data)
    except Exception as e:
        print(f"Error sending telegram msg: {e}")

# ==========================================
# 3. محرك OKX (The Engine) ⚙️
# ==========================================
api_key = "263d6dec-23fd-41fb-bd8d-6ba9f626ca1c"
secret_key = "68BF1CE4388551F4AE9B5E8E3AFD1F23"
password = "Olpolp2004$"

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
        sorted_tickers = sorted(valid_tickers, key=lambda x: tickers[x]['quoteVolume'], reverse=True)
        return sorted_tickers[:limit]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def analyze_market(symbol):
    """تحليل فني: RSI + Bollinger Bands"""
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        if not bars: return False, 0
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # المؤشرات
        bb = ta.bbands(df['close'], length=20, std=2)
        df = pd.concat([df, bb], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last_close = df['close'].iloc[-1]
        
        # --- تصحيح الخطأ هنا ---
        try:
            last_lower_bb = df['BBL_20_2.0'].iloc[-1]
        except KeyError:
             # أحياناً تتغير أسماء الأعمدة في pandas_ta
            last_lower_bb = df[df.columns[6]].iloc[-1] 
        # -----------------------

        last_rsi = df['rsi'].iloc[-1]
        
        # === شروط الدخول ===
        is_buy_signal = (last_close < last_lower_bb) and (last_rsi < 30)
        
        return is_buy_signal, last_close
        
    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return False, 0

def execute_futures_trade(symbol, leverage=10):
    """تنفيذ الصفقة All-in"""
    try:
        try:
            exchange.set_leverage(leverage, symbol)
            exchange.set_margin_mode('isolated', symbol)
        except: pass

        balance = exchange.fetch_balance()
        usdt_balance = balance['free']['USDT']
        
        if usdt_balance < 2: return "LOW_BALANCE"

        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        amount = (usdt_balance * leverage * 0.95) / price
        amount = exchange.amount_to_precision(symbol, amount)
        
        order = exchange.create_market_buy_order(symbol, amount)
        entry_price = float(order['average']) if order['average'] else price
        
        tp_price = entry_price * 1.015 
        sl_price = entry_price * 0.99 
        
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
    keep_alive()
    
    print("🤖 Bot started...")
    send_telegram_msg("🔥 تم تشغيل البوت (The Hunter) بنجاح!")
    
    while True:
        try:
            if check_open_positions() > 0:
                print("يوجد صفقة مفتوحة، الانتظار...")
                time.sleep(60)
                continue
            
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
                    break 
                
                time.sleep(1.5) 
            
            if not found:
                print("No opportunities. Sleeping 30s...")
                time.sleep(30)
                
        except Exception as e:
            print(f"Critical Error: {e}")
            time.sleep(10)
