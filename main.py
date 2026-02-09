#!/usr/bin/env python3
"""
⚡ EarlySpike — Crypto Signal Sniper Bot
Telegram + Twitter + OKX | Spot & Futures
"""

import os, sys, gc, json, time, sqlite3, asyncio, logging, threading, traceback
from io import BytesIO
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

try:
    import tweepy
except ImportError:
    tweepy = None

# ════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════

BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
FREE_CHANNEL_ID    = int(os.getenv("FREE_CHANNEL_ID", "0"))
PAID_CHANNEL_ID    = int(os.getenv("PAID_CHANNEL_ID", "0"))
FREE_GROUP_ID      = int(os.getenv("FREE_GROUP_ID", "0"))
ADMIN_IDS          = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip()]
PAID_CHANNEL_LINK  = os.getenv("PAID_CHANNEL_LINK", "https://t.me/EarlySpikePremium")
FREE_CHANNEL_LINK  = os.getenv("FREE_CHANNEL_LINK", "https://t.me/EarlySpike")

TW_API_KEY         = os.getenv("TW_API_KEY", "")
TW_API_SECRET      = os.getenv("TW_API_SECRET", "")
TW_ACCESS_TOKEN    = os.getenv("TW_ACCESS_TOKEN", "")
TW_ACCESS_SECRET   = os.getenv("TW_ACCESS_SECRET", "")
TW_BEARER          = os.getenv("TW_BEARER", "")

OKX_BASE           = "https://www.okx.com"

MAX_FREE_DAILY     = 20
MAX_TWEETS_DAILY   = 17
SCAN_INTERVAL      = 60        # seconds between full scans
MONITOR_INTERVAL   = 15        # seconds between TP/SL checks
SIGNAL_COOLDOWN    = 300       # per-symbol cooldown (seconds)
SIGNAL_EXPIRY_H    = 24        # close signal after N hours

VOL_SPIKE_MULT     = 3.0       # volume must be Nx average
MIN_PRICE_CHG      = 0.5       # minimum price change %
RSI_LO, RSI_HI     = 35, 72   # RSI optimal zone
MIN_SCORE           = 65       # minimum detection score

DB_FILE            = "earlyspike.db"
PORT               = int(os.getenv("PORT", "8080"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s ▸ %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("EarlySpike")

# ════════════════════════════════════════════════════════════
#  DATABASE  (SQLite + WAL — survives normal restarts)
# ════════════════════════════════════════════════════════════

class DB:
    def __init__(self, path: str = DB_FILE):
        self.path = path
        self.lock = threading.Lock()
        self._init()

    # ── helpers ──────────────────────────────────────────────
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def _run(self, sql, p=()):
        with self.lock:
            c = self._conn()
            try:
                cur = c.execute(sql, p); c.commit(); return cur
            finally:
                c.close()

    def _one(self, sql, p=()):
        with self.lock:
            c = self._conn()
            try:
                r = c.execute(sql, p).fetchone(); return dict(r) if r else None
            finally:
                c.close()

    def _all(self, sql, p=()):
        with self.lock:
            c = self._conn()
            try:
                return [dict(r) for r in c.execute(sql, p).fetchall()]
            finally:
                c.close()

    # ── schema ───────────────────────────────────────────────
    def _init(self):
        with self.lock:
            c = self._conn()
            try:
                c.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    mtype TEXT NOT NULL,
                    entry REAL NOT NULL,
                    tp1 REAL, tp2 REAL, tp3 REAL,
                    sl REAL,
                    volume REAL,
                    roi_spot REAL, roi_5x REAL, roi_10x REAL, roi_20x REAL,
                    status TEXT DEFAULT 'ACTIVE',
                    tp1_hit INT DEFAULT 0, tp2_hit INT DEFAULT 0, tp3_hit INT DEFAULT 0,
                    sl_hit INT DEFAULT 0,
                    free_mid INT, paid_mid INT, tweet_id TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    closed_at TEXT,
                    pnl REAL
                );
                CREATE TABLE IF NOT EXISTS users (
                    uid INTEGER PRIMARY KEY, uname TEXT, fname TEXT,
                    joined TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS daily (
                    day TEXT PRIMARY KEY,
                    free_sig INT DEFAULT 0, tweets INT DEFAULT 0, total INT DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS pending (
                    uid INTEGER PRIMARY KEY, cid INTEGER,
                    ts TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS ix_sig_status ON signals(status);
                CREATE INDEX IF NOT EXISTS ix_sig_sym    ON signals(symbol);
                """)
                c.commit()
            finally:
                c.close()

    # ── signals ──────────────────────────────────────────────
    def add_signal(self, d: dict) -> int:
        cur = self._run(
            """INSERT INTO signals
               (symbol,mtype,entry,tp1,tp2,tp3,sl,volume,roi_spot,roi_5x,roi_10x,roi_20x)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["symbol"],d["mtype"],d["entry"],d["tp1"],d["tp2"],d["tp3"],d["sl"],
             d.get("volume",0),d.get("roi_spot",0),d.get("roi_5x",0),
             d.get("roi_10x",0),d.get("roi_20x",0)))
        return cur.lastrowid

    def set_msg_ids(self, sid, free=None, paid=None, tw=None):
        parts, vals = [], []
        if free is not None: parts.append("free_mid=?"); vals.append(free)
        if paid is not None: parts.append("paid_mid=?"); vals.append(paid)
        if tw   is not None: parts.append("tweet_id=?"); vals.append(tw)
        if parts:
            vals.append(sid)
            self._run(f"UPDATE signals SET {','.join(parts)} WHERE id=?", tuple(vals))

    def active_signals(self):
        return self._all("SELECT * FROM signals WHERE status='ACTIVE'")

    def hit_tp(self, sid, n):
        self._run(f"UPDATE signals SET tp{n}_hit=1 WHERE id=?", (sid,))
        s = self._one("SELECT * FROM signals WHERE id=?", (sid,))
        if s and s["tp1_hit"] and s["tp2_hit"] and s["tp3_hit"]:
            self._run("UPDATE signals SET status='ALL_TP', closed_at=datetime('now') WHERE id=?", (sid,))

    def hit_sl(self, sid):
        self._run("UPDATE signals SET sl_hit=1, status='SL_HIT', closed_at=datetime('now') WHERE id=?", (sid,))

    def close_sig(self, sid, status, pnl=0):
        self._run("UPDATE signals SET status=?, closed_at=datetime('now'), pnl=? WHERE id=?",
                  (status, pnl, sid))

    def get_sig(self, sid):
        return self._one("SELECT * FROM signals WHERE id=?", (sid,))

    # ── daily counters ───────────────────────────────────────
    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def today_counts(self):
        d = self._today()
        r = self._one("SELECT * FROM daily WHERE day=?", (d,))
        if not r:
            self._run("INSERT OR IGNORE INTO daily(day) VALUES(?)", (d,))
            return {"day": d, "free_sig": 0, "tweets": 0, "total": 0}
        return r

    def inc(self, field):
        d = self._today()
        self._run(
            f"INSERT INTO daily(day,{field}) VALUES(?,1) "
            f"ON CONFLICT(day) DO UPDATE SET {field}={field}+1", (d,))

    # ── users ────────────────────────────────────────────────
    def add_user(self, uid, uname=None, fname=None):
        self._run("INSERT OR IGNORE INTO users(uid,uname,fname) VALUES(?,?,?)",
                  (uid, uname, fname))

    # ── verification ─────────────────────────────────────────
    def add_pending(self, uid, cid):
        self._run("INSERT OR REPLACE INTO pending(uid,cid) VALUES(?,?)", (uid, cid))

    def rm_pending(self, uid):
        self._run("DELETE FROM pending WHERE uid=?", (uid,))

    def expired_pending(self, mins=10):
        return self._all(
            "SELECT * FROM pending WHERE ts < datetime('now', ?)", (f"-{mins} minutes",))

    # ── stats ────────────────────────────────────────────────
    def stats(self):
        t = self._one("SELECT COUNT(*) c FROM signals")["c"]
        a = self._one("SELECT COUNT(*) c FROM signals WHERE status='ACTIVE'")["c"]
        w = self._one("SELECT COUNT(*) c FROM signals WHERE status IN ('ALL_TP','PARTIAL_TP')")["c"]
        l = self._one("SELECT COUNT(*) c FROM signals WHERE status='SL_HIT'")["c"]
        u = self._one("SELECT COUNT(*) c FROM users")["c"]
        wr = round(w / max(w + l, 1) * 100, 1)
        return dict(total=t, active=a, wins=w, losses=l, users=u, winrate=wr)


# ════════════════════════════════════════════════════════════
#  OKX PUBLIC API CLIENT
# ════════════════════════════════════════════════════════════

class OKX:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "EarlySpike/1.0"})
        self._cache: Dict[str, Any] = {}
        self._ctime: Dict[str, float] = {}
        self.TTL = 25

    def _get(self, ep, params=None):
        key = f"{ep}|{json.dumps(params or {}, sort_keys=True)}"
        now = time.time()
        if key in self._cache and now - self._ctime.get(key, 0) < self.TTL:
            return self._cache[key]
        try:
            r = self.s.get(f"{OKX_BASE}{ep}", params=params, timeout=15)
            r.raise_for_status()
            d = r.json()
            if d.get("code") == "0":
                self._cache[key] = d["data"]
                self._ctime[key] = now
                return d["data"]
            return []
        except Exception as e:
            log.warning(f"OKX ▸ {e}")
            return []

    def tickers(self, inst_type="SPOT"):
        return self._get("/api/v5/market/tickers", {"instType": inst_type})

    def ticker(self, inst_id):
        d = self._get("/api/v5/market/ticker", {"instId": inst_id})
        return d[0] if d else None

    def candles(self, inst_id, bar="5m", limit=100):
        return self._get("/api/v5/market/candles",
                         {"instId": inst_id, "bar": bar, "limit": str(limit)})

    def clear(self):
        self._cache.clear()
        self._ctime.clear()


# ════════════════════════════════════════════════════════════
#  TECHNICAL ANALYSIS ENGINE
# ════════════════════════════════════════════════════════════

class TA:
    @staticmethod
    def ema(data, period):
        out = np.empty_like(data)
        m = 2.0 / (period + 1)
        out[0] = data[0]
        for i in range(1, len(data)):
            out[i] = data[i] * m + out[i - 1] * (1 - m)
        return out

    @staticmethod
    def rsi(closes, period=14):
        d = np.diff(closes)
        gain = np.where(d > 0, d, 0.0)
        loss = np.where(d < 0, -d, 0.0)
        ag = np.zeros(len(closes))
        al = np.zeros(len(closes))
        if len(gain) < period:
            return np.full(len(closes), 50.0)
        ag[period] = gain[:period].mean()
        al[period] = loss[:period].mean()
        for i in range(period + 1, len(closes)):
            ag[i] = (ag[i - 1] * (period - 1) + gain[i - 1]) / period
            al[i] = (al[i - 1] * (period - 1) + loss[i - 1]) / period
        rs = np.where(al > 0, ag / al, 100.0)
        return 100.0 - 100.0 / (1.0 + rs)

    @staticmethod
    def macd(closes):
        e12 = TA.ema(closes, 12)
        e26 = TA.ema(closes, 26)
        line = e12 - e26
        sig = TA.ema(line, 9)
        return line, sig, line - sig  # macd, signal, histogram

    @staticmethod
    def bbands(closes, period=20, mult=2.0):
        sma = np.convolve(closes, np.ones(period) / period, mode="valid")
        sma = np.concatenate([np.full(period - 1, np.nan), sma])
        std = np.array([
            np.std(closes[max(0, i - period + 1):i + 1]) if i >= period - 1 else 0.0
            for i in range(len(closes))
        ])
        return sma + mult * std, sma, sma - mult * std  # upper, mid, lower

    @staticmethod
    def atr(highs, lows, closes, period=14):
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(np.abs(highs[1:] - closes[:-1]),
                       np.abs(lows[1:] - closes[:-1])))
        out = np.zeros(len(closes))
        if len(tr) >= period:
            out[period] = tr[:period].mean()
            for i in range(period + 1, len(closes)):
                out[i] = (out[i - 1] * (period - 1) + tr[i - 1]) / period
        return out

    @staticmethod
    def swing_levels(highs, lows, look=5):
        res, sup = [], []
        for i in range(look, len(highs) - look):
            if highs[i] == highs[i - look:i + look + 1].max():
                res.append(float(highs[i]))
            if lows[i] == lows[i - look:i + look + 1].min():
                sup.append(float(lows[i]))
        return TA._dedup(sorted(sup)), TA._dedup(sorted(res))

    @staticmethod
    def _dedup(lvls, thr=0.005):
        if not lvls:
            return lvls
        out = [lvls[0]]
        for v in lvls[1:]:
            if abs(v - out[-1]) / max(out[-1], 1e-12) > thr:
                out.append(v)
        return out

    @staticmethod
    def vol_spike(vols, period=20, mult=VOL_SPIKE_MULT):
        if len(vols) < period + 1:
            return False
        avg = vols[-(period + 1):-1].mean()
        return avg > 0 and vols[-1] > avg * mult

    @staticmethod
    def vol_increasing(vols, n=3):
        if len(vols) < n + 1:
            return False
        recent = vols[-n:]
        return all(recent[i] >= recent[i - 1] * 0.9 for i in range(1, len(recent)))

    @staticmethod
    def bullish_candles(opens, closes, n=5):
        if len(opens) < n:
            return 0
        return int(sum(1 for i in range(-n, 0) if closes[i] > opens[i]))


# ════════════════════════════════════════════════════════════
#  SIGNAL DETECTOR
# ════════════════════════════════════════════════════════════

@dataclass
class Signal:
    symbol: str
    mtype: str
    entry: float
    tp1: float
    tp2: float
    tp3: float
    sl: float
    volume: float
    roi_spot: float
    roi_5x: float
    roi_10x: float
    roi_20x: float
    score: int = 0
    candles_raw: list = None  # keep for chart

    def to_dict(self):
        return dict(symbol=self.symbol, mtype=self.mtype, entry=self.entry,
                    tp1=self.tp1, tp2=self.tp2, tp3=self.tp3, sl=self.sl,
                    volume=self.volume, roi_spot=self.roi_spot,
                    roi_5x=self.roi_5x, roi_10x=self.roi_10x, roi_20x=self.roi_20x)


class Detector:
    def __init__(self, okx: OKX, db: DB):
        self.okx = okx
        self.db = db
        self.cooldowns: Dict[str, float] = {}

    def _cd(self, sym):
        return time.time() - self.cooldowns.get(sym, 0) < SIGNAL_COOLDOWN

    def _set_cd(self, sym):
        self.cooldowns[sym] = time.time()

    def _clean_cd(self):
        now = time.time()
        self.cooldowns = {k: v for k, v in self.cooldowns.items() if now - v < SIGNAL_COOLDOWN}

    # ── main scan ────────────────────────────────────────────
    def scan(self) -> List[Signal]:
        out: List[Signal] = []
        self._clean_cd()
        for itype, label in [("SPOT", "SPOT"), ("SWAP", "FUTURES")]:
            try:
                tks = self.okx.tickers(itype)
                if not tks:
                    continue
                cands = []
                for t in tks:
                    iid = t.get("instId", "")
                    if "-USDT" not in iid:
                        continue
                    try:
                        last = float(t.get("last", 0))
                        v24 = float(t.get("volCcy24h", 0) or t.get("vol24h", 0))
                        if last <= 0:
                            continue
                        vol_usd = v24 if v24 > 10000 else v24 * last
                        minv = 50_000 if label == "SPOT" else 100_000
                        if vol_usd < minv:
                            continue
                        op24 = float(t.get("open24h", 0) or t.get("sodUtc0", 0))
                        chg = ((last - op24) / op24 * 100) if op24 > 0 else 0
                        cands.append({"id": iid, "last": last, "vol": vol_usd, "chg": chg})
                    except (ValueError, TypeError):
                        continue
                cands.sort(key=lambda x: x["vol"], reverse=True)
                for c in cands[:150]:
                    if self._cd(c["id"]):
                        continue
                    if self.db._one("SELECT 1 FROM signals WHERE symbol=? AND status='ACTIVE'",
                                    (c["id"],)):
                        continue
                    sig = self._analyze(c["id"], label)
                    if sig:
                        out.append(sig)
                        self._set_cd(c["id"])
                    time.sleep(0.12)  # rate-limit courtesy
            except Exception as e:
                log.error(f"Scan {itype}: {e}")
        out.sort(key=lambda s: s.score, reverse=True)
        gc.collect()
        return out

    # ── deep analysis ────────────────────────────────────────
    def _analyze(self, iid, mtype) -> Optional[Signal]:
        try:
            raw = self.okx.candles(iid, "5m", 100)
            if not raw or len(raw) < 50:
                return None
            raw = list(reversed(raw))  # oldest first

            o = np.array([float(c[1]) for c in raw])
            h = np.array([float(c[2]) for c in raw])
            l = np.array([float(c[3]) for c in raw])
            cl = np.array([float(c[4]) for c in raw])
            v = np.array([float(c[5]) for c in raw])

            price = cl[-1]
            if price <= 0:
                return None

            # ── scoring ──────────────────────────────────────
            score = 0

            # 1 ▸ volume spike  (max 35)
            if TA.vol_spike(v, 20, 5.0):
                score += 25
            elif TA.vol_spike(v, 20, VOL_SPIKE_MULT):
                score += 15
            if TA.vol_increasing(v, 3):
                score += 10

            # 2 ▸ price action  (max 30)
            ema20 = TA.ema(cl, 20)
            if price > ema20[-1]:
                score += 10
            bull = TA.bullish_candles(o, cl, 5)
            if bull >= 3:
                score += 10
            pchg = (price - cl[-6]) / cl[-6] * 100 if len(cl) >= 6 else 0
            if pchg > 1.0:
                score += 10
            elif pchg > MIN_PRICE_CHG:
                score += 5

            # 3 ▸ momentum  (max 25)
            rsi_arr = TA.rsi(cl)
            rsi_now = float(rsi_arr[-1])
            _, _, hist = TA.macd(cl)
            if hist[-1] > 0:
                score += 8
            if len(hist) >= 2 and hist[-1] > hist[-2]:
                score += 7
            if RSI_LO < rsi_now < RSI_HI:
                score += 10

            # 4 ▸ Bollinger breakout  (max 10)
            bb_up, _, _ = TA.bbands(cl)
            if not np.isnan(bb_up[-1]) and price > bb_up[-1]:
                score += 10
            else:
                atr_arr = TA.atr(h, l, cl)
                if atr_arr[-1] > 0 and atr_arr[-1] > atr_arr[-2] * 1.2:
                    score += 5

            if score < MIN_SCORE:
                return None

            # ── TP / SL ─────────────────────────────────────
            _atr = float(TA.atr(h, l, cl)[-1])
            if _atr <= 0:
                _atr = price * 0.01
            sups, ress = TA.swing_levels(h, l, 5)
            above = [r for r in ress if r > price * 1.005]

            if len(above) >= 3:
                tp1, tp2, tp3 = above[0], above[1], above[2]
            elif len(above) == 2:
                tp1, tp2 = above[0], above[1]
                tp3 = price + _atr * 4
            elif len(above) == 1:
                tp1 = above[0]
                tp2 = price + _atr * 2.5
                tp3 = price + _atr * 4
            else:
                tp1 = price + _atr * 1.5
                tp2 = price + _atr * 2.5
                tp3 = price + _atr * 4.0

            tp1 = max(tp1, price * 1.008)
            tp2 = max(tp2, tp1 * 1.005)
            tp3 = max(tp3, tp2 * 1.005)

            below = [s for s in sups if s < price * 0.995]
            if below:
                sl = max(below[-1], price - _atr * 2)
            else:
                sl = price - _atr * 2
            sl = max(sl, price * 0.92)
            sl = min(sl, price * 0.97)

            risk = price - sl
            reward = tp1 - price
            if risk <= 0 or reward / risk < 2.0:
                return None

            dec = self._dec(price)
            tp1, tp2, tp3, sl = (round(x, dec) for x in (tp1, tp2, tp3, sl))
            entry = round(price, dec)

            rspot = round((tp3 - entry) / entry * 100, 2)

            return Signal(
                symbol=iid, mtype=mtype, entry=entry,
                tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
                volume=float(v[-1]) * price,
                roi_spot=rspot, roi_5x=round(rspot * 5, 2),
                roi_10x=round(rspot * 10, 2), roi_20x=round(rspot * 20, 2),
                score=min(score, 100), candles_raw=raw,
            )
        except Exception as e:
            log.debug(f"Analyze {iid}: {e}")
            return None

    @staticmethod
    def _dec(p):
        if p >= 1000: return 2
        if p >= 1:    return 4
        if p >= 0.01: return 6
        return 8


# ════════════════════════════════════════════════════════════
#  CHART GENERATOR
# ════════════════════════════════════════════════════════════

C = {
    "bg": "#0d1117", "txt": "#c9d1d9", "grid": "#21262d",
    "up": "#00d984", "dn": "#ff4757", "tp": "#00d984",
    "sl": "#ff4757", "en": "#ffa502", "wm": "#1c2a3a", "acc": "#58a6ff",
}

def _mpf_style():
    mc = mpf.make_marketcolors(
        up=C["up"], down=C["dn"],
        edge={"up": C["up"], "down": C["dn"]},
        wick={"up": C["up"], "down": C["dn"]},
        volume={"up": C["up"], "down": C["dn"]},
    )
    return mpf.make_mpf_style(
        marketcolors=mc, facecolor=C["bg"], edgecolor=C["grid"],
        gridcolor=C["grid"], gridstyle="--", gridaxis="both", y_on_right=True,
        rc={"font.size": 10, "axes.labelcolor": C["txt"],
            "xtick.color": C["txt"], "ytick.color": C["txt"]},
    )

def _watermark(fig):
    fig.text(0.5, 0.5, "@EarlySpike", fontsize=44, color=C["wm"],
             ha="center", va="center", alpha=0.35, fontweight="bold",
             rotation=30, transform=fig.transFigure)

def chart_signal(candles_raw, sig: Signal) -> BytesIO:
    try:
        data = []
        for c_ in candles_raw[-65:]:
            ts = datetime.fromtimestamp(int(c_[0]) / 1000, tz=timezone.utc)
            data.append({"Date": ts, "Open": float(c_[1]), "High": float(c_[2]),
                         "Low": float(c_[3]), "Close": float(c_[4]),
                         "Volume": float(c_[5])})
        df = pd.DataFrame(data).set_index("Date")

        hlines = dict(
            hlines=[sig.tp1, sig.tp2, sig.tp3, sig.sl, sig.entry],
            colors=[C["tp"], C["tp"], C["tp"], C["sl"], C["en"]],
            linewidths=[1, 1, 1, 1.5, 1.5],
            linestyle=["--", "--", "--", "-", "-"],
            alpha=0.75,
        )
        fig, axes = mpf.plot(
            df, type="candle", style=_mpf_style(), volume=True,
            hlines=hlines, figsize=(12, 7), returnfig=True,
            tight_layout=True, panel_ratios=(4, 1),
        )
        ax = axes[0]
        n = len(df) - 1
        for lbl, val, col in [("TP3", sig.tp3, C["tp"]), ("TP2", sig.tp2, C["tp"]),
                               ("TP1", sig.tp1, C["tp"]), ("ENTRY", sig.entry, C["en"]),
                               ("SL", sig.sl, C["sl"])]:
            ax.annotate(f"  {lbl} {val}", xy=(n, val), fontsize=8,
                        color=col, fontweight="bold", va="center")
        sym = sig.symbol.replace("-", "/")
        ax.set_title(f"  {sym}  ┃  {sig.mtype}", fontsize=14,
                     fontweight="bold", color=C["acc"], loc="left", pad=10)
        _watermark(fig)
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150, facecolor=C["bg"],
                    bbox_inches="tight", pad_inches=0.3)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        log.error(f"Chart err: {e}")
        return _chart_fallback(sig)

def _chart_fallback(sig):
    fig, ax = plt.subplots(figsize=(12, 7), facecolor=C["bg"])
    ax.set_facecolor(C["bg"])
    for lbl, val, col in [("TP3", sig.tp3, C["tp"]), ("TP2", sig.tp2, C["tp"]),
                           ("TP1", sig.tp1, C["tp"]), ("ENTRY", sig.entry, C["en"]),
                           ("SL", sig.sl, C["sl"])]:
        ax.axhline(val, color=col, ls="--", lw=1.5, alpha=0.8)
        ax.text(0.02, val, f"  {lbl}: {val}", transform=ax.get_yaxis_transform(),
                fontsize=11, color=col, fontweight="bold", va="center")
    ax.set_ylim(sig.sl * 0.995, sig.tp3 * 1.005)
    ax.set_title(f"{sig.symbol.replace('-','/')} | {sig.mtype}",
                 fontsize=16, fontweight="bold", color=C["acc"])
    ax.tick_params(colors=C["txt"])
    _watermark(fig)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=C["bg"], bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

def chart_report(candles_raw, sd, results) -> BytesIO:
    try:
        if not candles_raw or len(candles_raw) < 10:
            return _report_fallback(sd, results)
        raw = list(candles_raw)
        if int(raw[0][0]) > int(raw[-1][0]):
            raw.reverse()
        data = []
        for c_ in raw[-80:]:
            ts = datetime.fromtimestamp(int(c_[0]) / 1000, tz=timezone.utc)
            data.append({"Date": ts, "Open": float(c_[1]), "High": float(c_[2]),
                         "Low": float(c_[3]), "Close": float(c_[4]),
                         "Volume": float(c_[5])})
        df = pd.DataFrame(data).set_index("Date")

        lvls, cols = [], []
        for n_ in (1, 2, 3):
            lvls.append(sd[f"tp{n_}"])
            cols.append("#00d984" if results.get(f"tp{n_}_hit") else "#555")
        lvls.append(sd["sl"])
        cols.append("#ff4757" if results.get("sl_hit") else "#555")
        lvls.append(sd["entry"])
        cols.append(C["en"])

        hlines = dict(hlines=lvls, colors=cols,
                      linewidths=[1.5]*len(lvls), linestyle=["--"]*len(lvls), alpha=0.8)
        fig, axes = mpf.plot(df, type="candle", style=_mpf_style(), volume=True,
                             hlines=hlines, figsize=(12, 7), returnfig=True,
                             tight_layout=True, panel_ratios=(4, 1))
        ax = axes[0]
        is_win = results.get("tp1_hit", False)
        tag = "✅ WIN" if is_win else "❌ LOSS"
        tcol = C["up"] if is_win else C["dn"]
        sym = sd["symbol"].replace("-", "/")
        ax.set_title(f"  {sym}  ┃  REPORT  ┃  {tag}", fontsize=14,
                     fontweight="bold", color=tcol, loc="left", pad=10)
        _watermark(fig)
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150, facecolor=C["bg"],
                    bbox_inches="tight", pad_inches=0.3)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        log.error(f"Report chart err: {e}")
        return _report_fallback(sd, results)

def _report_fallback(sd, results):
    fig, ax = plt.subplots(figsize=(12, 7), facecolor=C["bg"])
    ax.set_facecolor(C["bg"])
    w = results.get("tp1_hit", False)
    ax.text(0.5, 0.5, f"{'✅ WIN' if w else '❌ LOSS'}\n{sd['symbol'].replace('-','/')}",
            fontsize=30, ha="center", va="center",
            color=C["up"] if w else C["dn"], fontweight="bold", transform=ax.transAxes)
    _watermark(fig)
    ax.axis("off")
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=C["bg"], bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ════════════════════════════════════════════════════════════
#  TWITTER CLIENT
# ════════════════════════════════════════════════════════════

class Twitter:
    def __init__(self, db: DB):
        self.db = db
        self.ok = False
        self.client = None
        self.api1 = None
        if tweepy and TW_API_KEY and TW_ACCESS_TOKEN:
            try:
                self.client = tweepy.Client(
                    bearer_token=TW_BEARER, consumer_key=TW_API_KEY,
                    consumer_secret=TW_API_SECRET,
                    access_token=TW_ACCESS_TOKEN,
                    access_token_secret=TW_ACCESS_SECRET)
                auth = tweepy.OAuth1UserHandler(
                    TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET)
                self.api1 = tweepy.API(auth)
                self.ok = True
                log.info("✅ Twitter ready")
            except Exception as e:
                log.warning(f"Twitter init: {e}")

    def can_post(self):
        return self.ok and self.db.today_counts()["tweets"] < MAX_TWEETS_DAILY

    def post(self, sig: Signal, chart_buf: BytesIO = None) -> Optional[str]:
        if not self.can_post():
            return None
        try:
            sym = sig.symbol.replace("-", "/")
            tag = sig.symbol.replace("-", "").replace("/", "")
            txt = (
                f"🚀 #{tag} Signal\n\n"
                f"📊 {sym} | {sig.mtype}\n"
                f"💰 Entry: {sig.entry}\n\n"
                f"🎯 TP1: {sig.tp1}\n"
                f"🎯 TP2: {sig.tp2}\n"
                f"🎯 TP3: {sig.tp3}\n"
                f"🛑 SL: {sig.sl}\n\n"
                f"📈 ROI: {sig.roi_spot}% Spot | {sig.roi_5x}% 5x | {sig.roi_10x}% 10x\n\n"
                f"⚡ FREE signals ➜ {FREE_CHANNEL_LINK}\n\n"
                f"#Crypto #Trading #EarlySpike"
            )
            mid = None
            if chart_buf and self.api1:
                chart_buf.seek(0)
                media = self.api1.media_upload(filename="signal.png", file=chart_buf)
                mid = media.media_id
            resp = self.client.create_tweet(text=txt, media_ids=[mid] if mid else None)
            tid = str(resp.data["id"])
            self.db.inc("tweets")
            log.info(f"🐦 Tweet {tid}")
            return tid
        except Exception as e:
            log.error(f"Tweet err: {e}")
            return None


# ════════════════════════════════════════════════════════════
#  VOLUME / NUMBER FORMATTING UTILS
# ════════════════════════════════════════════════════════════

def fmt_vol(v):
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.2f}M"
    if v >= 1e3:  return f"${v/1e3:.1f}K"
    return f"${v:.0f}"


# ════════════════════════════════════════════════════════════
#  TELEGRAM BOT — Handlers & Senders
# ════════════════════════════════════════════════════════════

class Bot:
    def __init__(self, db: DB, okx: OKX, det: Detector, tw: Twitter):
        self.db = db
        self.okx = okx
        self.det = det
        self.tw = tw
        self.app: Optional[Application] = None

    def build(self) -> Application:
        self.app = Application.builder().token(BOT_TOKEN).build()
        a = self.app
        a.add_handler(CommandHandler("start", self.h_start))
        a.add_handler(CommandHandler("stats", self.h_stats))
        a.add_handler(CommandHandler("promote", self.h_promote))
        a.add_handler(CommandHandler("cancel", self.h_cancel))
        a.add_handler(CommandHandler("help", self.h_help))
        a.add_handler(CallbackQueryHandler(self.h_cb))
        a.add_handler(MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS, self.h_newmember))
        # catch-all for promote flow (group=1 so it doesn't block other handlers)
        a.add_handler(MessageHandler(
            filters.ALL & ~filters.COMMAND, self.h_msg), group=1)
        return a

    # ── /start ───────────────────────────────────────────────
    async def h_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        self.db.add_user(u.id, u.username, u.first_name)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢  Free Channel", url=FREE_CHANNEL_LINK)],
            [InlineKeyboardButton("👑  Premium 24/7", url=PAID_CHANNEL_LINK)],
        ])
        await update.message.reply_text(
            "⚡ <b>Welcome to EarlySpike!</b> ⚡\n\n"
            "The most <b>advanced crypto signal sniper</b> you'll ever use.\n\n"
            "🔍 We detect coins <b>minutes before they explode</b> using\n"
            "real-time volume analysis, multi-indicator momentum scoring,\n"
            "and smart TP/SL placement based on liquidity zones.\n\n"
            "📊 <b>Every signal includes:</b>\n"
            "• Beautiful chart with entry, 3 TPs & SL\n"
            "• ROI for Spot, 5x, 10x, 20x leverage\n"
            "• Live performance report once targets are hit\n\n"
            "💎 <b>Free Channel</b> — up to 20 signals/day\n"
            "👑 <b>Premium</b> — unlimited signals 24/7\n\n"
            "🚀 <i>Join now and never miss the next big move!</i>",
            parse_mode="HTML", reply_markup=kb)

    # ── /stats ───────────────────────────────────────────────
    async def h_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            return await update.message.reply_text("⛔")
        s = self.db.stats()
        t = self.db.today_counts()
        await update.message.reply_text(
            "📊 <b>EarlySpike Stats</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📈 Total: <b>{s['total']}</b>  ┃  🟢 Active: <b>{s['active']}</b>\n"
            f"✅ Wins: <b>{s['wins']}</b>  ┃  ❌ Losses: <b>{s['losses']}</b>\n"
            f"🎯 Win Rate: <b>{s['winrate']}%</b>\n"
            f"👥 Users: <b>{s['users']}</b>\n\n"
            f"📅 <b>Today</b>\n"
            f"📢 Free: {t['free_sig']}/{MAX_FREE_DAILY}  ┃  "
            f"🐦 Tweets: {t['tweets']}/{MAX_TWEETS_DAILY}  ┃  "
            f"📊 Total: {t['total']}",
            parse_mode="HTML")

    # ── /promote ─────────────────────────────────────────────
    async def h_promote(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            return await update.message.reply_text("⛔")
        ctx.user_data["promo"] = {"step": "content"}
        await update.message.reply_text(
            "📣 <b>Promote Mode</b>\n\n"
            "Send your content now (text / photo / video / GIF).\n"
            "Use /cancel to abort.", parse_mode="HTML")

    async def h_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        ctx.user_data.pop("promo", None)
        await update.message.reply_text("❌ Cancelled.")

    # ── /help ────────────────────────────────────────────────
    async def h_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "⚡ <b>EarlySpike Commands</b>\n\n"
            "/start — Welcome\n/stats — Statistics (admin)\n"
            "/promote — Send promo (admin)\n/help — This message",
            parse_mode="HTML")

    # ── callback queries ─────────────────────────────────────
    async def h_cb(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if q.data == "verify":
            uid = q.from_user.id
            self.db.rm_pending(uid)
            try:
                await ctx.bot.restrict_chat_member(
                    FREE_GROUP_ID, uid,
                    permissions=ChatPermissions(
                        can_send_messages=True, can_send_audios=True,
                        can_send_documents=True, can_send_photos=True,
                        can_send_videos=True, can_send_video_notes=True,
                        can_send_voice_notes=True, can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                        can_invite_users=True))
            except Exception as e:
                log.error(f"Unrestrict: {e}")
            await q.edit_message_text("✅ <b>Verified!</b> Welcome to EarlySpike 🚀",
                                      parse_mode="HTML")

    # ── new member verification ──────────────────────────────
    async def h_newmember(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.new_chat_members:
            return
        cid = update.effective_chat.id
        if cid != FREE_GROUP_ID:
            return
        for m in update.message.new_chat_members:
            if m.is_bot:
                continue
            self.db.add_pending(m.id, cid)
            try:
                await ctx.bot.restrict_chat_member(
                    cid, m.id,
                    permissions=ChatPermissions(
                        can_send_messages=False, can_send_audios=False,
                        can_send_documents=False, can_send_photos=False,
                        can_send_videos=False, can_send_video_notes=False,
                        can_send_voice_notes=False, can_send_polls=False,
                        can_send_other_messages=False,
                        can_add_web_page_previews=False))
            except Exception as e:
                log.error(f"Restrict: {e}")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Verify you're not a robot", callback_data="verify")]
            ])
            await update.message.reply_text(
                f"👋 Hey <b>{m.first_name}</b>!\n\n"
                f"🔒 Tap the button below to verify.\n"
                f"⏰ You have <b>10 minutes</b> or you'll be removed.",
                parse_mode="HTML", reply_markup=kb)

    # ── promote flow message handler ─────────────────────────
    async def h_msg(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        promo = ctx.user_data.get("promo")
        if not promo or update.effective_user.id not in ADMIN_IDS:
            return
        msg = update.message
        if not msg:
            return
        step = promo.get("step")

        if step == "content":
            promo["content"] = msg
            promo["step"] = "btn_text"
            await msg.reply_text(
                "✅ Content saved!\n\nSend <b>button text</b> or /skip for no button.",
                parse_mode="HTML")

        elif step == "btn_text":
            if msg.text and msg.text.strip() == "/skip":
                promo["btn_text"] = None
                promo["step"] = "target"
                await msg.reply_text("Now send the <b>target chat ID</b>.", parse_mode="HTML")
            else:
                promo["btn_text"] = msg.text
                promo["step"] = "btn_url"
                await msg.reply_text("Send the <b>button URL</b> or <b>chat ID</b>.",
                                     parse_mode="HTML")

        elif step == "btn_url":
            url = msg.text.strip()
            if not url.startswith("http"):
                url = f"https://t.me/{url.lstrip('@')}"
            promo["btn_url"] = url
            promo["step"] = "target"
            await msg.reply_text("Send the <b>target chat ID</b>.", parse_mode="HTML")

        elif step == "target":
            target = msg.text.strip()
            content: Any = promo["content"]
            kb = None
            if promo.get("btn_text") and promo.get("btn_url"):
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(promo["btn_text"], url=promo["btn_url"])]
                ])
            try:
                tid = int(target)
                if content.photo:
                    await ctx.bot.send_photo(tid, content.photo[-1].file_id,
                                             caption=content.caption or "",
                                             parse_mode="HTML", reply_markup=kb)
                elif content.video:
                    await ctx.bot.send_video(tid, content.video.file_id,
                                             caption=content.caption or "",
                                             parse_mode="HTML", reply_markup=kb)
                elif content.animation:
                    await ctx.bot.send_animation(tid, content.animation.file_id,
                                                 caption=content.caption or "",
                                                 parse_mode="HTML", reply_markup=kb)
                elif content.document:
                    await ctx.bot.send_document(tid, content.document.file_id,
                                                caption=content.caption or "",
                                                parse_mode="HTML", reply_markup=kb)
                else:
                    await ctx.bot.send_message(tid, content.text or "",
                                               parse_mode="HTML", reply_markup=kb)
                await msg.reply_text(f"✅ Sent to <code>{target}</code>!", parse_mode="HTML")
            except Exception as e:
                await msg.reply_text(f"❌ Error: <code>{e}</code>", parse_mode="HTML")
            ctx.user_data.pop("promo", None)

    # ═══════════════ SIGNAL SENDERS ══════════════════════════

    async def send_signal(self, sig: Signal, chart_buf: BytesIO):
        sym = sig.symbol.replace("-", "/")
        vol = fmt_vol(sig.volume)
        txt = (
            f"⚡ <b>EARLYSPIKE SIGNAL</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊  <b>{sym}</b>\n"
            f"📈  {sig.mtype}\n"
            f"💰  Vol: <b>{vol}</b>\n\n"
            f"🟢  Entry: <code>{sig.entry}</code>\n\n"
            f"🎯  TP1:  <code>{sig.tp1}</code>\n"
            f"🎯  TP2:  <code>{sig.tp2}</code>\n"
            f"🎯  TP3:  <code>{sig.tp3}</code>\n"
            f"🛑  SL:    <code>{sig.sl}</code>\n\n"
            f"💵  ROI  <b>{sig.roi_spot}%</b>  ┃  SPOT\n"
            f"💵  ROI  <b>{sig.roi_5x}%</b>  ┃  5x Leverage\n"
            f"💵  ROI  <b>{sig.roi_10x}%</b>  ┃  10x Leverage\n"
            f"💵  ROI  <b>{sig.roi_20x}%</b>  ┃  20x Leverage\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ @EarlySpike"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 Premium Signals 24/7", url=PAID_CHANNEL_LINK)]
        ])

        sid = self.db.add_signal(sig.to_dict())
        free_mid = paid_mid = tw_id = None

        # → paid channel (always)
        try:
            chart_buf.seek(0)
            m = await self.app.bot.send_photo(
                PAID_CHANNEL_ID, chart_buf, caption=txt, parse_mode="HTML")
            paid_mid = m.message_id
            self.db.inc("total")
        except Exception as e:
            log.error(f"Paid send: {e}")

        # → free channel (daily limit)
        cnt = self.db.today_counts()
        if cnt["free_sig"] < MAX_FREE_DAILY:
            try:
                chart_buf.seek(0)
                m = await self.app.bot.send_photo(
                    FREE_CHANNEL_ID, chart_buf, caption=txt,
                    parse_mode="HTML", reply_markup=kb)
                free_mid = m.message_id
                self.db.inc("free_sig")
            except Exception as e:
                log.error(f"Free send: {e}")

        # → twitter
        chart_buf.seek(0)
        tw_id = self.tw.post(sig, chart_buf)

        self.db.set_msg_ids(sid, free=free_mid, paid=paid_mid, tw=tw_id)
        log.info(f"✅ Signal #{sid} | {sig.symbol} | score={sig.score}")

    async def send_report(self, sd: dict, res: dict, chart_buf: BytesIO):
        sym = sd["symbol"].replace("-", "/")
        lines_tp = []
        for n in (1, 2, 3):
            hit = res.get(f"tp{n}_hit", False)
            price = sd[f"tp{n}"]
            lines_tp.append(f"{'✅' if hit else '❌'}  TP{n}  <code>{price}</code>  "
                            f"{'HIT ✓' if hit else '—'}")
        sl_line = f"{'🔴  SL HIT ✗' if res.get('sl_hit') else '🟢  SL  Safe'}"

        if res.get("sl_hit"):
            pnl = (sd["sl"] - sd["entry"]) / sd["entry"] * 100
            status = "🔴 STOPPED OUT"
        elif all(res.get(f"tp{n}_hit") for n in (1, 2, 3)):
            pnl = (sd["tp3"] - sd["entry"]) / sd["entry"] * 100
            status = "🏆 ALL TARGETS HIT"
        elif res.get("tp2_hit"):
            pnl = (sd["tp2"] - sd["entry"]) / sd["entry"] * 100
            status = "✅ PARTIAL PROFIT"
        elif res.get("tp1_hit"):
            pnl = (sd["tp1"] - sd["entry"]) / sd["entry"] * 100
            status = "✅ PARTIAL PROFIT"
        else:
            pnl = 0
            status = "⚪ EXPIRED"

        is_w = res.get("tp1_hit", False)
        hdr = "🏆" if is_w else "📊"

        txt = (
            f"{hdr} <b>SIGNAL REPORT</b> {hdr}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊  <b>{sym}</b>  ┃  {sd['mtype']}\n\n"
            f"🟢  Entry: <code>{sd['entry']}</code>\n\n"
            + "\n".join(lines_tp) + "\n"
            f"{sl_line}\n\n"
            f"📈  Status: <b>{status}</b>\n"
            f"💰  PnL: <b>{pnl:+.2f}%</b> (Spot)\n"
            f"💰  PnL: <b>{pnl*5:+.2f}%</b> (5x)\n"
            f"💰  PnL: <b>{pnl*10:+.2f}%</b> (10x)\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ @EarlySpike"
        )

        for ch_id, mid_key in [(PAID_CHANNEL_ID, "paid_mid"), (FREE_CHANNEL_ID, "free_mid")]:
            mid = sd.get(mid_key)
            if not mid:
                continue
            try:
                chart_buf.seek(0)
                await self.app.bot.send_photo(
                    ch_id, chart_buf, caption=txt, parse_mode="HTML",
                    reply_to_message_id=mid)
            except Exception:
                try:
                    chart_buf.seek(0)
                    await self.app.bot.send_photo(
                        ch_id, chart_buf, caption=txt, parse_mode="HTML")
                except Exception as e:
                    log.error(f"Report to {ch_id}: {e}")


# ════════════════════════════════════════════════════════════
#  SIGNAL MONITOR — tracks TP/SL for active signals
# ════════════════════════════════════════════════════════════

class Monitor:
    def __init__(self, db: DB, okx: OKX, bot: Bot):
        self.db = db
        self.okx = okx
        self.bot = bot

    async def tick(self):
        for sig in self.db.active_signals():
            try:
                tk = self.okx.ticker(sig["symbol"])
                if not tk:
                    continue
                price = float(tk.get("last", 0))
                if price <= 0:
                    continue

                changed = False

                # TP checks
                if not sig["tp1_hit"] and price >= sig["tp1"]:
                    self.db.hit_tp(sig["id"], 1); sig["tp1_hit"] = 1; changed = True
                if not sig["tp2_hit"] and sig["tp1_hit"] and price >= sig["tp2"]:
                    self.db.hit_tp(sig["id"], 2); sig["tp2_hit"] = 1; changed = True
                if not sig["tp3_hit"] and sig["tp2_hit"] and price >= sig["tp3"]:
                    self.db.hit_tp(sig["id"], 3); sig["tp3_hit"] = 1; changed = True

                # SL check
                if not sig["sl_hit"] and price <= sig["sl"]:
                    self.db.hit_sl(sig["id"]); sig["sl_hit"] = 1; changed = True

                # Expiry
                created = datetime.fromisoformat(sig["created_at"])
                if datetime.utcnow() - created > timedelta(hours=SIGNAL_EXPIRY_H):
                    st = "PARTIAL_TP" if sig["tp1_hit"] else "EXPIRED"
                    self.db.close_sig(sig["id"], st)
                    changed = True

                # Send report if closed
                if changed:
                    fresh = self.db.get_sig(sig["id"])
                    if fresh and fresh["status"] != "ACTIVE":
                        await self._report(fresh)
            except Exception as e:
                log.error(f"Monitor {sig['symbol']}: {e}")

    async def _report(self, sd):
        try:
            candles = self.okx.candles(sd["symbol"], "5m", 100)
            if candles:
                candles = list(reversed(candles))
            res = {f"tp{n}_hit": bool(sd[f"tp{n}_hit"]) for n in (1, 2, 3)}
            res["sl_hit"] = bool(sd["sl_hit"])
            buf = chart_report(candles, sd, res)
            await self.bot.send_report(sd, res, buf)
            log.info(f"📋 Report #{sd['id']} {sd['symbol']}")
        except Exception as e:
            log.error(f"Report gen: {e}")


# ════════════════════════════════════════════════════════════
#  MEMORY MANAGER
# ════════════════════════════════════════════════════════════

def mem_clean():
    plt.close("all")
    gc.collect()


# ════════════════════════════════════════════════════════════
#  FLASK KEEP-ALIVE  (for UptimeRobot / Render)
# ════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def _home():
    return (
        "<h1>⚡ EarlySpike</h1>"
        f"<p>Running | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>"
    )

@flask_app.route("/health")
def _health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ════════════════════════════════════════════════════════════
#  VERIFICATION CLEANER
# ════════════════════════════════════════════════════════════

async def clean_pending(app: Application, db: DB):
    for e in db.expired_pending(10):
        try:
            await app.bot.ban_chat_member(e["cid"], e["uid"])
            await asyncio.sleep(1)
            await app.bot.unban_chat_member(e["cid"], e["uid"], only_if_banned=True)
            log.info(f"👢 Kicked unverified {e['uid']}")
        except Exception as ex:
            log.error(f"Kick: {ex}")
        finally:
            db.rm_pending(e["uid"])


# ════════════════════════════════════════════════════════════
#  MAIN ASYNC LOOP
# ════════════════════════════════════════════════════════════

async def engine(bot: Bot, det: Detector, mon: Monitor, db: DB):
    log.info("🚀 Engine started")
    cycle = 0

    while True:
        try:
            cycle += 1

            # ── scan for new signals ─────────────────────────
            log.info(f"🔍 Scan #{cycle}")
            signals = det.scan()
            if signals:
                log.info(f"📡 {len(signals)} candidates")
                for sig in signals[:5]:
                    try:
                        candles_raw = sig.candles_raw
                        if not candles_raw:
                            candles_raw = det.okx.candles(sig.symbol, "5m", 100)
                            if candles_raw:
                                candles_raw = list(reversed(candles_raw))
                        buf = chart_signal(candles_raw or [], sig)
                        await bot.send_signal(sig, buf)
                        await asyncio.sleep(2)
                    except Exception as e:
                        log.error(f"Signal send {sig.symbol}: {e}")

            # ── monitor loop (4 × 15s = 60s) ────────────────
            for _ in range(4):
                await mon.tick()
                await asyncio.sleep(MONITOR_INTERVAL)

            # ── verification cleanup every 5 cycles ──────────
            if cycle % 5 == 0:
                await clean_pending(bot.app, db)

            # ── memory cleanup every 30 cycles ───────────────
            if cycle % 30 == 0:
                mem_clean()
                det.okx.clear()
                log.info("🧹 Memory cleaned")

        except Exception as e:
            log.error(f"Engine: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(30)


# ════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════

def main():
    log.info("⚡ EarlySpike starting…")

    db  = DB()
    okx = OKX()
    det = Detector(okx, db)
    tw  = Twitter(db)
    bot = Bot(db, okx, det, tw)
    mon = Monitor(db, okx, bot)
    app = bot.build()

    # Flask keep-alive in background thread
    threading.Thread(target=run_flask, daemon=True).start()
    log.info(f"🌐 Flask on port {PORT}")

    async def post_init(application: Application):
        asyncio.create_task(engine(bot, det, mon, db))
        log.info("✅ EarlySpike fully operational!")

    app.post_init = post_init
    log.info("🤖 Starting polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()