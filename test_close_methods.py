# test_close_methods.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Патчим все методы закрытия
def trace_calls(frame, event, arg):
    if event == 'call':
        func_name = frame.f_code.co_name
        if 'close' in func_name.lower() or 'emergency' in func_name.lower():
            print(f"📞 ВЫЗОВ: {func_name} из {frame.f_back.f_code.co_name if frame.f_back else '?'}")
    return trace_calls

import sys
sys.settrace(trace_calls)

# Импортируем и запускаем бота
from trading_bot.bot import trading_bot
# ... дальше тест