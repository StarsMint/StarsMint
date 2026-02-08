import sqlite3
import json

DB_NAME = "trading_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # جدول الإعدادات والاستراتيجية
    c.execute('''CREATE TABLE IF NOT EXISTS strategy (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_profit REAL,
        coins TEXT,
        is_active INTEGER DEFAULT 0
    )''')
    
    # جدول الصفقات
    c.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        entry_price REAL,
        quantity REAL,
        tp_price REAL,
        status TEXT, -- OPEN, CLOSED
        buy_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        close_time TIMESTAMP,
        profit_usdt REAL,
        profit_percent REAL
    )''')
    
    conn.commit()
    conn.close()

def get_strategy():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM strategy LIMIT 1")
    data = c.fetchone()
    conn.close()
    return data

def create_strategy(target_profit, coins_list):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM strategy") # حذف القديم إن وجد
    c.execute("INSERT INTO strategy (target_profit, coins, is_active) VALUES (?, ?, 1)", 
              (target_profit, json.dumps(coins_list)))
    conn.commit()
    conn.close()

def delete_strategy():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM strategy")
    conn.commit()
    conn.close()
