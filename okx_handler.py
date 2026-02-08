import ccxt
import pandas as pd
import pandas_ta as ta
import time
import os

# إعدادات الاتصال
api_key = os.environ.get('OKX_API_KEY')
secret_key = os.environ.get('OKX_SECRET_KEY')
password = os.environ.get('OKX_PASSWORD')

exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret_key,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}  # تفعيل الفيوتشرز (Perpetual Swaps)
})

# دالة لجلب العملات النشطة (Top Volatile)
def get_top_volatile_coins(limit=30):
    try:
        tickers = exchange.fetch_tickers()
        # نختار فقط أزواج USDT ونرتبها حسب الفوليوم
        valid_tickers = [
            symbol for symbol in tickers 
            if '/USDT:USDT' in symbol  # صيغة الفيوتشرز في OKX
        ]
        
        # ترتيب حسب حجم التداول (Volume) لأخذ العملات الحية فقط
        sorted_tickers = sorted(valid_tickers, key=lambda x: tickers[x]['quoteVolume'], reverse=True)
        return sorted_tickers[:limit]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

# دالة التحليل الفني "النووي"
def analyze_market(symbol):
    try:
        # نجلب فريم 5 دقائق للسرعة، أو 15 دقيقة للثبات
        bars = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. Bollinger Bands (لمعرفة الانفجار)
        bb = ta.bbands(df['close'], length=20, std=2)
        df = pd.concat([df, bb], axis=1)
        
        # 2. RSI (لمعرفة التشبع)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        # 3. MACD (لتأكيد الاتجاه)
        macd = ta.macd(df['close'])
        df = pd.concat([df, macd], axis=1)
        
        # القراءات الأخيرة
        last_close = df['close'].iloc[-1]
        last_lower_bb = df['BBL_20_2.0'].iloc[-1]
        last_rsi = df['rsi'].iloc[-1]
        last_macd = df['MACD_12_26_9'].iloc[-1]
        last_signal = df['MACDs_12_26_9'].iloc[-1]
        
        # === استراتيجية القنص (Reversal Sniper) ===
        # شروط الشراء (Long):
        # 1. السعر نزل تحت خط البولنجر السفلي (انفجار لأسفل مبالغ فيه)
        # 2. RSI تحت 30 (تشبع بيعي قوي)
        # 3. (اختياري) بداية تقاطع إيجابي في الماكد
        
        is_buy_signal = (last_close < last_lower_bb) and (last_rsi < 30)
        
        return is_buy_signal, last_close
        
    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return False, 0

# دالة تنفيذ الصفقة (All-in Futures)
def execute_futures_trade(symbol, leverage=10):
    try:
        # 1. ضبط الرافعة المالية (مهم جداً)
        try:
            exchange.set_leverage(leverage, symbol)
            exchange.set_margin_mode('isolated', symbol) # معزول لحماية باقي الرصيد
        except:
            pass # قد تكون مضبوطة مسبقاً

        # 2. معرفة الرصيد
        balance = exchange.fetch_balance()
        usdt_balance = balance['free']['USDT']
        
        if usdt_balance < 2: return "LOW_BALANCE"

        # 3. حساب الكمية (All-in)
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        
        # المبلغ المتاح * الرافعة = القوة الشرائية
        buying_power = usdt_balance * leverage 
        amount = (buying_power * 0.95) / price # نترك هامش أمان
        
        amount = exchange.amount_to_precision(symbol, amount)
        
        # 4. دخول الصفقة (Market Order)
        order = exchange.create_market_buy_order(symbol, amount)
        
        # 5. وضع أهداف البيع (TP/SL)
        # بما أننا فيوتشرز، الهدف والستوب لازم يكونوا دقيقين
        entry_price = float(order['average']) if order['average'] else price
        
        # هدف 1.5% ربح (مع الرافعة 10x يعني 15% ربح فعلي)
        tp_price = entry_price * 1.015 
        # وقف خسارة 1% (مع الرافعة 10x يعني 10% خسارة)
        sl_price = entry_price * 0.99 
        
        # أوامر الخروج
        try:
            exchange.create_order(symbol, 'limit', 'sell', amount, tp_price, params={'reduceOnly': True})
            exchange.create_order(symbol, 'stop', 'sell', amount, sl_price, params={'stopPrice': sl_price, 'reduceOnly': True})
        except Exception as e:
            return f"⚠️ تم الشراء لكن فشل وضع TP/SL: {e}"

        return f"🚀 تم دخول Long على {symbol}\nسعر الدخول: {entry_price}\nالهدف: {tp_price}\nالوقف: {sl_price}"

    except Exception as e:
        return f"❌ خطأ في التنفيذ: {e}"

def check_open_positions():
    # دالة لتفقد الأرباح وإغلاق الصفقات
    try:
        positions = exchange.fetch_positions()
        msg = ""
        count = 0
        for pos in positions:
            if float(pos['contracts']) > 0: # إذا في صفقة مفتوحة
                symbol = pos['symbol']
                pnl = pos['unrealizedPnl']
                roe = pos['percentage']
                msg += f"🔹 {symbol}: {pnl} USDT ({roe}%)\n"
                count += 1
        return count, msg
    except:
        return 0, ""

