import ccxt
import pandas as pd
import numpy as np
import requests
import time
import gc
from datetime import datetime
from scipy.optimize import curve_fit

# ---------------------------------------------------------
# [CONFIG] منطقة الإعدادات السرية
# ---------------------------------------------------------
API_KEY = "263d6dec-23fd-41fb-bd8d-6ba9f626ca1c"
SECRET_KEY = "68BF1CE4388551F4AE9B5E8E3AFD1F23"
PASSPHRASE = "Olpolp2004$" 

TELEGRAM_TOKEN = "8576670268:AAEITh1HLZ29Mu_muscP9sls7oE8ku_lY2g"
CHAT_ID = "1801208219"

# إعدادات المحاكاة
INITIAL_BALANCE = 20.0  # رصيد البداية
LEVERAGE = 1  # تداول فوري (سبوت) للمحاكاة العلمية
FEE_RATE = 0.002  # 0.1% رسوم OKX

# ---------------------------------------------------------
# [MATH CORE] المحرك الرياضي (Hurst + Kalman)
# ---------------------------------------------------------

def get_hurst_exponent(time_series):
    """
    حساب أُس هيرست لكشف ذاكرة السوق.
    H < 0.5: ارتداد للمتوسط (Mean Reverting) - مناسب للكالمان.
    H > 0.5: اتجاه (Trending).
    """
    lags = range(2, 20)
    tau = [np.sqrt(np.std(np.subtract(time_series[lag:], time_series[:-lag]))) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return poly[0] * 2.0

def kalman_filter(data, process_variance=1e-5, measurement_variance=1e-3):
    """
    فلتر كالمان أحادي البعد لاستخراج 'السعر الحقيقي' من الضوضاء.
    """
    n_iter = len(data)
    sz = (n_iter,)
    xhat = np.zeros(sz)      # تقدير الحالة
    P = np.zeros(sz)         # تقدير التباين
    xhatminus = np.zeros(sz) # تقدير الحالة الأولية
    Pminus = np.zeros(sz)    # تقدير التباين الأولي
    K = np.zeros(sz)         # ربح كالمان

    xhat[0] = data[0]
    P[0] = 1.0

    for k in range(1, n_iter):
        # التحديث الزمني
        xhatminus[k] = xhat[k-1]
        Pminus[k] = P[k-1] + process_variance

        # تحديث القياس
        K[k] = Pminus[k] / (Pminus[k] + measurement_variance)
        xhat[k] = xhatminus[k] + K[k] * (data[k] - xhatminus[k])
        P[k] = (1 - K[k]) * Pminus[k]

    return xhat

# ---------------------------------------------------------
# [BOT BRAIN] العقل المدبر
# ---------------------------------------------------------

class QuantBot:
    def __init__(self):
        self.exchange = ccxt.okx({
            'apiKey': API_KEY,
            'secret': SECRET_KEY,
            'password': PASSPHRASE,
            'enableRateLimit': True,
        })
        self.balance = INITIAL_BALANCE
        self.active_trade = None
        self.trades_history = []
        self.running = True
        self.pairs = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT'] # الأزواج المستهدفة

    def send_msg(self, text):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Error sending msg: {e}")

    def fetch_data(self, symbol, limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='5m', limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df['close'].values
        except Exception as e:
            print(f"Error fetching data for {symbol}: {e}")
            return []

    def analyze_market(self):
        best_opportunity = None
        highest_score = 0

        self.send_msg("📡 <b>جاري مسح السوق بحثاً عن الشذوذ الرياضي...</b>")

        for symbol in self.pairs:
            prices = self.fetch_data(symbol)
            if len(prices) < 50: continue

            # تطبيق المعادلات
            hurst = get_hurst_exponent(prices)
            kalman = kalman_filter(prices)
            current_price = prices[-1]
            true_value = kalman[-1]
            
            # حساب الانحراف المعياري للفرق بين السعر وكالمان
            spread = prices - kalman
            std_dev = np.std(spread)
            z_score = (current_price - true_value) / std_dev

            # استراتيجية الارتداد (Mean Reversion)
            # نبحث عن Hurst < 0.5 (سوق عرضي) وانحراف قوي (Z-Score)
            
            score = 0
            signal = None
            
            if hurst < 0.5: # شرط الذاكرة العكسية
                if z_score < -2.0: # السعر أقل من قيمته الحقيقية بـ 2 انحراف معياري
                    score = abs(z_score) * (0.5 - hurst)
                    signal = 'BUY'
                elif z_score > 2.0: # السعر أعلى من قيمته الحقيقية
                    score = abs(z_score) * (0.5 - hurst)
                    signal = 'SELL' # في السبوت نستخدم هذا للخروج فقط أو الشورت (هنا نركز عالشراء)

            if signal == 'BUY' and score > highest_score:
                highest_score = score
                best_opportunity = {
                    'symbol': symbol,
                    'price': current_price,
                    'kalman': true_value,
                    'z_score': z_score,
                    'hurst': hurst,
                    'target': true_value, # العودة لخط كالمان
                    'stop': current_price - (2 * std_dev) # وقف خسارة رياضي
                }

        return best_opportunity

    def execute_trade_simulation(self, opportunity):
        symbol = opportunity['symbol']
        entry_price = opportunity['price']
        
        # حساب الرسوم
        cost = self.balance * FEE_RATE
        net_balance = self.balance - cost
        quantity = net_balance / entry_price
        
        self.active_trade = {
            'symbol': symbol,
            'entry_price': entry_price,
            'quantity': quantity,
            'target': opportunity['target'],
            'stop': opportunity['stop'],
            'start_time': datetime.now(),
            'hurst_at_entry': opportunity['hurst']
        }
        
        msg = (
            f"🚀 <b>تم رصد فرصة حتمية (Mathematical Edge)</b>\n"
            f"--------------------------------\n"
            f"🔹 <b>الزوج:</b> {symbol}\n"
            f"🔹 <b>الدلالة العلمية (Z):</b> {opportunity['z_score']:.2f}\n"
            f"🔹 <b>ذاكرة السوق (Hurst):</b> {opportunity['hurst']:.2f}\n"
            f"💵 <b>سعر الدخول:</b> {entry_price}\n"
            f"🎯 <b>الهدف (Kalman True Value):</b> {opportunity['target']:.2f}\n"
            f"🛑 <b>وقف الخسارة (2-Sigma):</b> {opportunity['stop']:.2f}\n"
            f"💰 <b>الرصيد المحاكى:</b> {self.balance:.2f}$\n"
            f"📉 <b>الرسوم المخصومة:</b> {cost:.4f}$"
        )
        self.send_msg(msg)

    def monitor_trade(self):
        if not self.active_trade: return

        prices = self.fetch_data(self.active_trade['symbol'], limit=20)
        current_price = prices[-1]
        
        # تحديث قيمة كالمان (الهدف متحرك ديناميكياً)
        kalman = kalman_filter(prices)
        dynamic_target = kalman[-1]

        # شروط الخروج
        profit_pct = (current_price - self.active_trade['entry_price']) / self.active_trade['entry_price'] * 100
        
        # 1. تحقق الهدف (العودة للمتوسط)
        if current_price >= dynamic_target:
            self.close_trade(current_price, "Target Hit (Mean Reversion)")
        # 2. ضرب وقف الخسارة
        elif current_price <= self.active_trade['stop']:
            self.close_trade(current_price, "Stop Loss (Statistical Failure)")
        else:
            # تقرير الحالة كل 5 دقائق
            msg = (
                f"⏱ <b>تحديث الإحداثيات (5m Update)</b>\n"
                f"الزوج: {self.active_trade['symbol']}\n"
                f"السعر الحالي: {current_price}\n"
                f"الهدف الديناميكي: {dynamic_target:.2f}\n"
                f"الربح/الخسارة: {profit_pct:.2f}%"
            )
            self.send_msg(msg)

    def close_trade(self, exit_price, reason):
        trade = self.active_trade
        gross_value = trade['quantity'] * exit_price
        fee = gross_value * FEE_RATE
        net_value = gross_value - fee
        
        profit_amount = net_value - self.balance # مقارنة بالرصيد قبل الصفقة (الذي لم يخصم منه سوى رسوم الدخول)
        # تصحيح دقيق: الرصيد السابق كان عند الدخول. الآن الرصيد الجديد
        self.balance = net_value
        
        win = profit_amount > 0
        emoji = "✅" if win else "❌"
        
        self.trades_history.append(win)
        
        msg = (
            f"{emoji} <b>إغلاق صفقة ({reason})</b>\n"
            f"--------------------------------\n"
            f"سعر الخروج: {exit_price}\n"
            f"الرصيد الجديد: {self.balance:.2f}$\n"
            f"صافي الربح/الخسارة: {profit_amount:.2f}$"
        )
        self.send_msg(msg)
        self.active_trade = None

    def get_status_report(self):
        wins = sum(self.trades_history)
        total = len(self.trades_history)
        win_rate = (wins/total*100) if total > 0 else 0
        profit = self.balance - INITIAL_BALANCE
        
        return (
            f"📊 <b>تقرير الحالة الكلي</b>\n"
            f"عدد الصفقات: {total}\n"
            f"نسبة النجاح: {win_rate:.1f}%\n"
            f"الرصيد الحالي: {self.balance:.2f}$\n"
            f"صافي الربح الكلي: {profit:.2f}$"
        )

    def run(self):
        self.send_msg("🤖 <b>تم تشغيل النظام الكمي (The Quantitative Engine)</b>\nانتظر جلب البيانات...")
        
        # معالجة الأوامر من التيليجرام (محاكاة بسيطة عبر التحديثات)
        last_update_id = 0
        
        while self.running:
            # 1. فحص أوامر التيليجرام (/stop, /status)
            try:
                updates = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id+1}").json()
                if "result" in updates:
                    for update in updates["result"]:
                        last_update_id = update["update_id"]
                        if "message" in update and "text" in update["message"]:
                            text = update["message"]["text"]
                            if text == "/stop":
                                self.send_msg("🛑 تم إيقاف النظام.")
                                self.running = False
                            elif text == "/status":
                                self.send_msg(self.get_status_report())
            except:
                pass

            if not self.running: break

            # 2. منطق التداول
            if self.active_trade:
                self.monitor_trade()
            else:
                # إذا لم تكن هناك صفقة، ابحث عن فرصة
                self.send_msg(f"🔍 <b>إحداثيات ما قبل الدخول:</b>\nجاري حساب معادلات الضغط (Z-Score) لـ {len(self.pairs)} أزواج...")
                opportunity = self.analyze_market()
                if opportunity:
                    self.execute_trade_simulation(opportunity)
                else:
                    self.send_msg("⚠️ لا توجد فرص تتوافق مع الشروط الرياضية الصارمة حالياً.")

            # 3. تنظيف الرام والانتظار
            gc.collect()
            time.sleep(300) # 5 دقائق انتظار

# تشغيل البوت
if __name__ == "__main__":
    bot = QuantBot()
    bot.run()
