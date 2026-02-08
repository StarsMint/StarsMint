#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
    🦈 SHARK HUNTER BOT v3.0 - Advanced Futures Scalper
    ⚡ Multi-Strategy | Low RAM | High Precision
═══════════════════════════════════════════════════════════════
"""

import os
import time
import random
import threading
import requests
import ccxt
from collections import deque
from datetime import datetime
import gc

# ══════════════════════════════════════════════════════════════
# 🌐 KEEP ALIVE SERVER
# ══════════════════════════════════════════════════════════════
from flask import Flask
app = Flask('')

@app.route('/')
def home():
    return "🦈 Shark Hunter Bot is ALIVE!"

def run_http():
    app.run(host='0.0.0.0', port=8080, threaded=True)

def keep_alive():
    threading.Thread(target=run_http, daemon=True).start()

# ══════════════════════════════════════════════════════════════
# 📢 TELEGRAM HANDLER
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = "8053838829:AAHo1iTJm958nIBgOoinGZpwTdm467lCBT4"
TELEGRAM_CHAT_ID = 1801208219

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except:
        pass

# ══════════════════════════════════════════════════════════════
# ⚙️ EXCHANGE ENGINE
# ══════════════════════════════════════════════════════════════
exchange = ccxt.okx({
    'apiKey': "263d6dec-23fd-41fb-bd8d-6ba9f626ca1c",
    'secret': "68BF1CE4388551F4AE9B5E8E3AFD1F23",
    'password': "Olpolp2004$",
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

# ══════════════════════════════════════════════════════════════
# 📊 ULTRA-LIGHT TECHNICAL ANALYSIS (NO PANDAS!)
# ══════════════════════════════════════════════════════════════
class LightAnalyzer:
    """تحليل فني خفيف بدون pandas - استهلاك RAM أقل بـ 90%"""
    
    @staticmethod
    def sma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period
    
    @staticmethod
    def ema(data, period):
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = (price * multiplier) + (ema_val * (1 - multiplier))
        return ema_val
    
    @staticmethod
    def rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            gains.append(max(0, delta))
            losses.append(max(0, -delta))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def bollinger_bands(closes, period=20, std_dev=2):
        if len(closes) < period:
            return None, None, None
        sma = sum(closes[-period:]) / period
        variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
        std = variance ** 0.5
        return sma - (std_dev * std), sma, sma + (std_dev * std)
    
    @staticmethod
    def macd(closes, fast=12, slow=26, signal=9):
        if len(closes) < slow + signal:
            return 0, 0, 0
        
        ema_fast = LightAnalyzer.ema(closes, fast)
        ema_slow = LightAnalyzer.ema(closes, slow)
        
        if ema_fast is None or ema_slow is None:
            return 0, 0, 0
            
        macd_line = ema_fast - ema_slow
        
        # Simplified signal line
        macd_values = []
        for i in range(slow, len(closes)):
            ef = LightAnalyzer.ema(closes[:i+1], fast)
            es = LightAnalyzer.ema(closes[:i+1], slow)
            if ef and es:
                macd_values.append(ef - es)
        
        if len(macd_values) >= signal:
            signal_line = sum(macd_values[-signal:]) / signal
        else:
            signal_line = macd_line
            
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    
    @staticmethod
    def atr(highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_list.append(tr)
        return sum(tr_list[-period:]) / period
    
    @staticmethod
    def stochastic(highs, lows, closes, k_period=14, d_period=3):
        if len(closes) < k_period:
            return 50, 50
        
        lowest_low = min(lows[-k_period:])
        highest_high = max(highs[-k_period:])
        
        if highest_high == lowest_low:
            return 50, 50
            
        k = ((closes[-1] - lowest_low) / (highest_high - lowest_low)) * 100
        
        # Simplified %D
        k_values = []
        for i in range(k_period, len(closes) + 1):
            ll = min(lows[i-k_period:i])
            hh = max(highs[i-k_period:i])
            if hh != ll:
                k_values.append(((closes[i-1] - ll) / (hh - ll)) * 100)
        
        d = sum(k_values[-d_period:]) / d_period if len(k_values) >= d_period else k
        return k, d
    
    @staticmethod
    def volume_profile(volumes, period=20):
        if len(volumes) < period:
            return 1
        avg_vol = sum(volumes[-period:]) / period
        current_vol = volumes[-1]
        return current_vol / avg_vol if avg_vol > 0 else 1
    
    @staticmethod
    def support_resistance(highs, lows, closes, lookback=50):
        if len(closes) < lookback:
            return closes[-1] * 0.98, closes[-1] * 1.02
        
        recent_lows = lows[-lookback:]
        recent_highs = highs[-lookback:]
        
        support = min(recent_lows)
        resistance = max(recent_highs)
        
        return support, resistance
    
    @staticmethod
    def trend_strength(closes, period=20):
        """قوة الترند: 1 = صعود قوي، -1 = هبوط قوي، 0 = جانبي"""
        if len(closes) < period:
            return 0
        
        sma_short = sum(closes[-period//2:]) / (period//2)
        sma_long = sum(closes[-period:]) / period
        
        price = closes[-1]
        
        if price > sma_short > sma_long:
            return 1  # صعود
        elif price < sma_short < sma_long:
            return -1  # هبوط
        return 0  # جانبي


# ══════════════════════════════════════════════════════════════
# 🎯 MULTI-STRATEGY SIGNAL GENERATOR
# ══════════════════════════════════════════════════════════════
class SignalGenerator:
    """مولد الإشارات متعدد الاستراتيجيات"""
    
    def __init__(self):
        self.analyzer = LightAnalyzer()
    
    def strategy_oversold_bounce(self, closes, highs, lows, volumes):
        """استراتيجية 1: ارتداد من التشبع البيعي"""
        rsi = self.analyzer.rsi(closes)
        bb_lower, bb_mid, bb_upper = self.analyzer.bollinger_bands(closes)
        stoch_k, stoch_d = self.analyzer.stochastic(highs, lows, closes)
        vol_ratio = self.analyzer.volume_profile(volumes)
        
        if bb_lower is None:
            return 0, "NO_DATA"
        
        score = 0
        reasons = []
        
        # RSI تشبع بيعي قوي
        if rsi < 25:
            score += 3
            reasons.append(f"RSI={rsi:.1f}🔥")
        elif rsi < 30:
            score += 2
            reasons.append(f"RSI={rsi:.1f}")
        
        # السعر تحت البولنجر السفلي
        if closes[-1] < bb_lower:
            score += 2
            reasons.append("BB_LOW✓")
        
        # Stochastic تشبع بيعي
        if stoch_k < 20 and stoch_d < 20:
            score += 2
            reasons.append(f"STOCH={stoch_k:.0f}")
        
        # حجم تداول مرتفع (تأكيد)
        if vol_ratio > 1.5:
            score += 1
            reasons.append(f"VOL={vol_ratio:.1f}x")
        
        return score, " | ".join(reasons)
    
    def strategy_macd_reversal(self, closes):
        """استراتيجية 2: انعكاس MACD"""
        macd, signal, hist = self.analyzer.macd(closes)
        
        score = 0
        reasons = []
        
        # MACD crosses above signal
        if macd > signal and hist > 0:
            score += 2
            reasons.append("MACD_CROSS↑")
        
        # Histogram turning positive
        if len(closes) > 30:
            prev_hist = self.analyzer.macd(closes[:-1])[2]
            if hist > 0 and prev_hist < 0:
                score += 3
                reasons.append("HIST_FLIP🔥")
        
        return score, " | ".join(reasons) if reasons else "NO_SIGNAL"
    
    def strategy_support_bounce(self, closes, highs, lows):
        """استراتيجية 3: ارتداد من الدعم"""
        support, resistance = self.analyzer.support_resistance(highs, lows, closes)
        atr = self.analyzer.atr(highs, lows, closes)
        current_price = closes[-1]
        
        score = 0
        reasons = []
        
        # السعر قريب من الدعم
        distance_to_support = (current_price - support) / current_price
        if distance_to_support < 0.005:  # أقل من 0.5%
            score += 3
            reasons.append(f"SUPPORT_TOUCH🎯")
        elif distance_to_support < 0.01:
            score += 2
            reasons.append(f"NEAR_SUPPORT")
        
        # تأكيد بشمعة انعكاسية (السعر الحالي أعلى من الافتتاح)
        if len(closes) > 1 and closes[-1] > closes[-2]:
            score += 1
            reasons.append("BULLISH_CANDLE")
        
        return score, " | ".join(reasons) if reasons else "NO_SIGNAL"
    
    def strategy_momentum_breakout(self, closes, highs, lows, volumes):
        """استراتيجية 4: اختراق بزخم"""
        vol_ratio = self.analyzer.volume_profile(volumes)
        trend = self.analyzer.trend_strength(closes)
        rsi = self.analyzer.rsi(closes)
        
        score = 0
        reasons = []
        
        # ترند صاعد + RSI ليس مشبعاً
        if trend == 1 and 40 < rsi < 70:
            score += 2
            reasons.append("UPTREND✓")
        
        # اختراق بحجم عالي
        if vol_ratio > 2.0:
            score += 2
            reasons.append(f"HIGH_VOL={vol_ratio:.1f}x🔥")
        
        # السعر يخترق القمة السابقة
        if closes[-1] > max(highs[-10:-1]):
            score += 3
            reasons.append("BREAKOUT↑")
        
        return score, " | ".join(reasons) if reasons else "NO_SIGNAL"
    
    def get_combined_signal(self, ohlcv_data):
        """تحليل شامل ودمج جميع الاستراتيجيات"""
        if len(ohlcv_data) < 50:
            return None
        
        opens = [x[1] for x in ohlcv_data]
        highs = [x[2] for x in ohlcv_data]
        lows = [x[3] for x in ohlcv_data]
        closes = [x[4] for x in ohlcv_data]
        volumes = [x[5] for x in ohlcv_data]
        
        total_score = 0
        all_reasons = []
        
        # تطبيق جميع الاستراتيجيات
        s1, r1 = self.strategy_oversold_bounce(closes, highs, lows, volumes)
        s2, r2 = self.strategy_macd_reversal(closes)
        s3, r3 = self.strategy_support_bounce(closes, highs, lows)
        s4, r4 = self.strategy_momentum_breakout(closes, highs, lows, volumes)
        
        total_score = s1 + s2 + s3 + s4
        
        if r1 != "NO_DATA" and r1 != "NO_SIGNAL":
            all_reasons.append(f"[1]{r1}")
        if r2 != "NO_SIGNAL":
            all_reasons.append(f"[2]{r2}")
        if r3 != "NO_SIGNAL":
            all_reasons.append(f"[3]{r3}")
        if r4 != "NO_SIGNAL":
            all_reasons.append(f"[4]{r4}")
        
        # حساب نسب TP/SL ديناميكية
        atr = self.analyzer.atr(highs, lows, closes)
        current_price = closes[-1]
        
        # TP/SL بناءً على ATR
        tp_multiplier = 2.0 if total_score >= 10 else 1.5
        sl_multiplier = 1.0
        
        tp_pct = (atr * tp_multiplier / current_price) * 100
        sl_pct = (atr * sl_multiplier / current_price) * 100
        
        # حدود معقولة
        tp_pct = max(1.0, min(5.0, tp_pct))
        sl_pct = max(0.5, min(2.0, sl_pct))
        
        return {
            'score': total_score,
            'reasons': " | ".join(all_reasons),
            'price': current_price,
            'tp_pct': tp_pct,
            'sl_pct': sl_pct,
            'atr': atr
        }


# ══════════════════════════════════════════════════════════════
# 💰 POSITION MANAGER
# ══════════════════════════════════════════════════════════════
class PositionManager:
    """إدارة الصفقات والمخاطر"""
    
    def __init__(self, exchange_obj):
        self.exchange = exchange_obj
        self.max_positions = 1
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0
    
    def get_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['free'].get('USDT', 0))
        except:
            return 0
    
    def has_open_position(self):
        try:
            positions = self.exchange.fetch_positions()
            for pos in positions:
                if float(pos.get('contracts', 0)) > 0:
                    return True
            return False
        except:
            return False
    
    def calculate_position_size(self, balance, leverage, risk_pct=0.95):
        """حساب حجم الصفقة"""
        return balance * risk_pct
    
    def execute_long(self, symbol, leverage, tp_pct, sl_pct):
        """تنفيذ صفقة شراء"""
        try:
            # إعداد الرافعة
            try:
                self.exchange.set_leverage(leverage, symbol)
                self.exchange.set_margin_mode('isolated', symbol)
            except:
                pass
            
            balance = self.get_balance()
            if balance < 2:
                return None, "رصيد غير كافٍ"
            
            ticker = self.exchange.fetch_ticker(symbol)
            price = ticker['last']
            
            # حساب الكمية
            position_value = balance * leverage * 0.95
            amount = position_value / price
            amount = self.exchange.amount_to_precision(symbol, amount)
            
            # تنفيذ الشراء
            order = self.exchange.create_market_buy_order(symbol, amount)
            entry_price = float(order['average']) if order.get('average') else price
            
            # حساب TP/SL
            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)
            
            # وضع أوامر TP/SL
            try:
                # Take Profit
                self.exchange.create_order(
                    symbol, 'limit', 'sell', amount, 
                    self.exchange.price_to_precision(symbol, tp_price),
                    params={'reduceOnly': True}
                )
                # Stop Loss
                self.exchange.create_order(
                    symbol, 'stop', 'sell', amount,
                    self.exchange.price_to_precision(symbol, sl_price),
                    params={'stopPrice': self.exchange.price_to_precision(symbol, sl_price), 'reduceOnly': True}
                )
            except Exception as e:
                return {
                    'entry': entry_price,
                    'tp': tp_price,
                    'sl': sl_price,
                    'amount': amount
                }, f"⚠️ TP/SL Error: {e}"
            
            return {
                'entry': entry_price,
                'tp': tp_price,
                'sl': sl_price,
                'amount': amount
            }, "SUCCESS"
            
        except Exception as e:
            return None, str(e)


# ══════════════════════════════════════════════════════════════
# 🔍 MARKET SCANNER
# ══════════════════════════════════════════════════════════════
class MarketScanner:
    """ماسح السوق الذكي"""
    
    def __init__(self, exchange_obj):
        self.exchange = exchange_obj
        self.signal_gen = SignalGenerator()
        self.cache = {}
        self.cache_time = {}
    
    def get_tradeable_pairs(self, min_volume_usdt=5000000):
        """جلب الأزواج القابلة للتداول مع فلترة بالحجم"""
        try:
            tickers = self.exchange.fetch_tickers()
            pairs = []
            
            for symbol, data in tickers.items():
                if '/USDT:USDT' not in symbol:
                    continue
                if data.get('quoteVolume', 0) < min_volume_usdt:
                    continue
                
                # استبعاد العملات المستقرة
                base = symbol.split('/')[0]
                if base in ['USDC', 'BUSD', 'DAI', 'TUSD', 'USDD']:
                    continue
                
                pairs.append({
                    'symbol': symbol,
                    'volume': data['quoteVolume'],
                    'change': data.get('percentage', 0)
                })
            
            # ترتيب حسب الحجم
            pairs.sort(key=lambda x: x['volume'], reverse=True)
            return pairs[:40]  # أعلى 40 زوج
            
        except Exception as e:
            print(f"Error fetching pairs: {e}")
            return []
    
    def scan_for_opportunities(self, min_score=7):
        """مسح السوق للفرص"""
        pairs = self.get_tradeable_pairs()
        opportunities = []
        
        for pair_data in pairs:
            symbol = pair_data['symbol']
            
            try:
                # جلب البيانات
                ohlcv = self.exchange.fetch_ohlcv(symbol, '5m', limit=100)
                
                if not ohlcv or len(ohlcv) < 50:
                    continue
                
                # تحليل
                signal = self.signal_gen.get_combined_signal(ohlcv)
                
                if signal and signal['score'] >= min_score:
                    opportunities.append({
                        'symbol': symbol,
                        'signal': signal,
                        'volume': pair_data['volume']
                    })
                
                # تنظيف الذاكرة
                del ohlcv
                gc.collect()
                
                time.sleep(0.5)  # تجنب rate limit
                
            except Exception as e:
                continue
        
        # ترتيب حسب قوة الإشارة
        opportunities.sort(key=lambda x: x['signal']['score'], reverse=True)
        return opportunities


# ══════════════════════════════════════════════════════════════
# 🤖 MAIN BOT
# ══════════════════════════════════════════════════════════════
class SharkHunterBot:
    """البوت الرئيسي"""
    
    def __init__(self):
        self.exchange = exchange
        self.scanner = MarketScanner(exchange)
        self.position_mgr = PositionManager(exchange)
        self.running = True
        
        # إعدادات
        self.LEVERAGE = 20  # رافعة عالية للأرباح السريعة ⚠️
        self.MIN_SCORE = 8  # حد أدنى للإشارة
        self.SCAN_INTERVAL = 30  # ثواني بين كل مسح
    
    def format_trade_msg(self, symbol, signal, trade_result):
        """تنسيق رسالة التداول"""
        return f"""
🦈 <b>SHARK HUNTER - NEW TRADE</b>

💎 <b>Symbol:</b> {symbol}
📊 <b>Score:</b> {signal['score']}/15
📈 <b>Entry:</b> ${trade_result['entry']:.6f}
🎯 <b>TP:</b> ${trade_result['tp']:.6f} (+{signal['tp_pct']:.2f}%)
🛡 <b>SL:</b> ${trade_result['sl']:.6f} (-{signal['sl_pct']:.2f}%)

📝 <b>Signals:</b>
{signal['reasons']}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    
    def run(self):
        """التشغيل الرئيسي"""
        print("🦈 SHARK HUNTER BOT v3.0 Started!")
        send_telegram("🦈 <b>SHARK HUNTER BOT v3.0</b>\n\n✅ Bot Started Successfully!\n⚡ Scanning for opportunities...")
        
        scan_count = 0
        
        while self.running:
            try:
                # فحص وجود صفقة مفتوحة
                if self.position_mgr.has_open_position():
                    print("📊 Position open, monitoring...")
                    time.sleep(30)
                    continue
                
                scan_count += 1
                print(f"\n🔍 Scan #{scan_count} - {datetime.now().strftime('%H:%M:%S')}")
                
                # طباعة الرصيد
                balance = self.position_mgr.get_balance()
                print(f"💰 Balance: ${balance:.2f}")
                
                if balance < 2:
                    print("⚠️ Low balance! Waiting...")
                    send_telegram(f"⚠️ رصيد منخفض: ${balance:.2f}")
                    time.sleep(60)
                    continue
                
                # مسح السوق
                opportunities = self.scanner.scan_for_opportunities(self.MIN_SCORE)
                
                if not opportunities:
                    print(f"😴 No opportunities found. Next scan in {self.SCAN_INTERVAL}s")
                    time.sleep(self.SCAN_INTERVAL)
                    continue
                
                # أفضل فرصة
                best = opportunities[0]
                symbol = best['symbol']
                signal = best['signal']
                
                print(f"\n⚡ OPPORTUNITY FOUND!")
                print(f"   Symbol: {symbol}")
                print(f"   Score: {signal['score']}")
                print(f"   TP: {signal['tp_pct']:.2f}% | SL: {signal['sl_pct']:.2f}%")
                
                # تنفيذ الصفقة
                trade_result, status = self.position_mgr.execute_long(
                    symbol, 
                    self.LEVERAGE,
                    signal['tp_pct'],
                    signal['sl_pct']
                )
                
                if trade_result:
                    msg = self.format_trade_msg(symbol, signal, trade_result)
                    print(f"✅ Trade executed!")
                    send_telegram(msg)
                else:
                    print(f"❌ Trade failed: {status}")
                    send_telegram(f"❌ فشل التنفيذ: {status}")
                
                # انتظار قبل المسح التالي
                time.sleep(60)
                
                # تنظيف الذاكرة
                gc.collect()
                
            except KeyboardInterrupt:
                print("\n👋 Shutting down...")
                self.running = False
                break
                
            except Exception as e:
                print(f"❌ Error: {e}")
                time.sleep(10)


# ══════════════════════════════════════════════════════════════
# 🚀 MAIN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    keep_alive()
    
    bot = SharkHunterBot()
    
    # يمكنك تعديل هذه الإعدادات:
    bot.LEVERAGE = 15      # رافعة (10-20 للمضاربة السريعة)
    bot.MIN_SCORE = 7      # حد أدنى للإشارة (5-10، كلما زاد = أكثر دقة لكن فرص أقل)
    bot.SCAN_INTERVAL = 20 # ثواني بين المسح
    
    bot.run()