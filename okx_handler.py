import ccxt
import pandas as pd
import pandas_ta as ta
import time
import json
import sqlite3
from datetime import datetime

# إعدادات الاتصال
exchange = ccxt.okx({
    'apiKey': '263d6dec-23fd-41fb-bd8d-6ba9f626ca1c',
    'secret': '68BF1CE4388551F4AE9B5E8E3AFD1F23',
    'password': 'Olpolp2004$',
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

DB_NAME = "trading_bot.db"

def get_server_status():
    try:
        start = time.time()
        exchange.fetch_time()
        ping = int((time.time() - start) * 1000)
        
        balance = exchange.fetch_balance()
        total_usdt = balance['total'].get('USDT', 0)
        free_usdt = balance['free'].get('USDT', 0)
        
        return ping, total_usdt, free_usdt
    except Exception as e:
        return 0, 0, 0

def check_market_conditions(symbol):
    try:
        # جلب شمعات الساعة (أو الإطار الزمني المفضل)
        bars = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # حساب RSI
        df['rsi'] = ta.rsi(df['close'], length=14)
        current_rsi = df['rsi'].iloc[-1]
        
        # استراتيجية بسيطة: شراء إذا كان RSI منخفض (تشبع بيعي)
        # يمكنك إضافة تحليل الفوليوم هنا
        if current_rsi < 40: 
            return True, current_rsi
        return False, current_rsi
    except:
        return False, 50

def execute_trade(symbol, target_net_profit_percent):
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['free']['USDT']
        
        if usdt_balance < 2: # أقل حد للتداول
            return "LOW_BALANCE"

        # 1. جلب سعر السوق الحالي
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker['ask'] # نشتري من ask
        
        # 2. حساب الكمية (Compound Interest: كل الرصيد)
        amount_to_spend = usdt_balance * 0.99 # نترك هامش بسيط جداً للعمولات لتجنب فشل الأمر
        amount = amount_to_spend / current_price
        
        # ضبط الدقة (Precision) مهم جداً في OKX
        market = exchange.market(symbol)
        amount = exchange.amount_to_precision(symbol, amount)

        # 3. تنفيذ أمر الشراء (Market Buy)
        order = exchange.create_market_buy_order(symbol, amount)
        actual_price = order['average'] if order['average'] else current_price
        filled_qty = float(order['filled'])
        
        # 4. حساب سعر البيع (TP)
        # المعادلة: سعر الدخول * (1 + (الربح المستهدف + رسوم الشراء + رسوم البيع))
        # رسوم OKX للمتداول العادي تقريباً 0.1% لكل طرف (0.2% مجموع)
        fees_buffer = 0.002 # 0.2%
        target_gross = (target_net_profit_percent / 100) + fees_buffer
        tp_price = actual_price * (1 + target_gross)
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # 5. وضع أمر البيع فوراً (Limit Sell)
        sell_order = exchange.create_limit_sell_order(symbol, filled_qty, tp_price)
        
        # حفظ في قاعدة البيانات
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''INSERT INTO trades (symbol, entry_price, quantity, tp_price, status, profit_usdt)
                     VALUES (?, ?, ?, ?, 'OPEN', 0)''', 
                     (symbol, actual_price, filled_qty, tp_price))
        conn.commit()
        conn.close()
        
        return f"✅ تم شراء {symbol}\nسعر الدخول: {actual_price}\nالهدف: {tp_price}\nالكمية: {filled_qty}"
        
    except Exception as e:
        return f"❌ فشل الشراء: {str(e)}"

def check_open_orders_status():
    # دالة تتفقد هل تم بيع العملة (تحقق الهدف) أم لا
    # إذا تحققت، تحدث قاعدة البيانات لتغلق الصفقة وتصبح جاهزة للصفقة التالية
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, symbol FROM trades WHERE status = 'OPEN'")
    open_trade = c.fetchone()
    
    msg = None
    if open_trade:
        trade_id, symbol = open_trade
        try:
            # نبحث في الطلبات المفتوحة في المنصة
            open_orders = exchange.fetch_open_orders(symbol)
            if not open_orders:
                # إذا لم يكن هناك طلبات مفتوحة، يعني أن أمر البيع تنفذ!
                c.execute("UPDATE trades SET status='CLOSED', close_time=CURRENT_TIMESTAMP WHERE id=?", (trade_id,))
                conn.commit()
                msg = f"💰 تم تحقيق الهدف لعملة {symbol}! الصفقة أغلقت، جاري البحث عن فرصة جديدة."
        except Exception as e:
            print(f"Error checking order: {e}")
            
    conn.close()
    return msg
