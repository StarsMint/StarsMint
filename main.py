import telegram_bot
import okx_handler
import time
import random
from keep_alive import keep_alive

# تشغيل السيرفر الوهمي
keep_alive()

def run_bot():
    print("🤖 Bot started analyzing Futures Market...")
    telegram_bot.send_msg("🔥 تم تفعيل بوت الصياد (Futures Mode)!")
    
    while True:
        try:
            # 1. تفقد الصفقات المفتوحة
            open_positions_count, status_msg = okx_handler.check_open_positions()
            
            # إذا في صفقات مفتوحة، نهدأ ونراقب
            if open_positions_count > 0:
                print(f"Positions open: {open_positions_count}")
                # ممكن ترسل تقرير كل فترة، بس حالياً ننتظر
                time.sleep(60) 
                continue

            # 2. البحث عن فرص (Scan)
            print("🔎 Scanning market for pumps/dumps...")
            # نجلب أفضل 30 عملة عليها حركة
            volatile_coins = okx_handler.get_top_volatile_coins(limit=30)
            
            # نخلطهم عشان العدالة
            random.shuffle(volatile_coins)
            
            opportunity_found = False
            
            for symbol in volatile_coins:
                print(f"Checking {symbol}...")
                is_buy, price = okx_handler.analyze_market(symbol)
                
                if is_buy:
                    telegram_bot.send_msg(f"⚡️ فرصة مكتشفة على {symbol}!\nالسعر: {price}\nجاري التنفيذ...")
                    
                    # تنفيذ الصفقة بكل الرصيد
                    result = okx_handler.execute_futures_trade(symbol, leverage=10) # رافعة 10
                    telegram_bot.send_msg(result)
                    
                    opportunity_found = True
                    break # نكتفي بصفقة واحدة في المرة
                
                time.sleep(1) # راحة بسيطة لتجنب الحظر
            
            if not opportunity_found:
                print("No opportunities found. Waiting...")
                time.sleep(30) # انتظار قبل المسح التالي

        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
