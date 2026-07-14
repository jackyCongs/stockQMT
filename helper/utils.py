# coding=utf-8

import logging
from datetime import time as d_time
from helper.time_utils import get_time, get_datetime

last_print_times = {}
logger = logging.getLogger(__name__)

MARKET_TIMES = {
    "morning_open": d_time(9, 30),
    "morning_close": d_time(11, 30),
    "afternoon_open": d_time(13, 0),
    "afternoon_close": d_time(15, 0),
    "buffer_morning_open": d_time(9, 35),
    "buffer_afternoon_open": d_time(13, 1),
    "gonna_close": d_time(14, 52),
    "call_auction": d_time(14, 57),
}


def enhance_stock_code(code, type='stock', ignore_warning = False):
    if type == 'index':
        if code.startswith('H'):
            return f"{code}.SH"
        if code.startswith('000'):
            return f"{code}.SH"
        if code.startswith('399'):
            return f"{code}.SZ"
        if code.startswith("9"):
            return f"{code}.SH"
        if not ignore_warning:
            logger.warning(f"Ticker [{code}] with type [{type}] has no corresponding market source.")
        return code
    # SSE (Shanghai Stock Exchange)
    if len(code) == 6 and (code.startswith('6') or code.startswith('900') or code.startswith('5')):
        return f"{code}.SH"
    # SZSE (Shenzhen Stock Exchange)
    elif len(code) == 6 and (code.startswith('00') or code.startswith('3') or code.startswith('158')
                             or code.startswith('159') or code.startswith('16')):
        return f"{code}.SZ"
    # BSE (Beijing Stock Exchange)
    elif len(code) == 6 and code.startswith(('8', '4', '92', '93')):
        return f"{code}.BJ"
    # Bonds and other instruments (treasury bonds, corporate bonds, convertible bonds, etc.)
    elif len(code) == 6 and code.startswith(('11', '12', '13', '14', '17', '18', '19', '01', '02', '10')):
        # Simple heuristic: prefix 123/127/128/112 is usually SZSE; otherwise, it is likely SSE
        if code.startswith(('123', '127', '128', '112')):
            return f"{code}.SZ"
        return f"{code}.SH"
    logger.warning(f"Ticker [{code}] with type [{type}] has no corresponding market source.")
    return code

def is_normal_a_share(code):
    """
    Determine if a component stock is a standard A-share stock.
    Excludes bonds, gold, HK stocks, US stocks, funds, etc.
    Standard A-share prefix:
    - Shanghai (SSE): 60, 68
    - Shenzhen (SZSE): 00, 30
    - Beijing (BSE): 8, 4, 92, 93
    """
    code_str = str(code).strip().split('.')[0]
    if not code_str.isdigit():
        return False
        
    if len(code_str) == 6:
        if code_str.startswith(('60', '68', '00', '30')):
            return True
        if code_str.startswith(('8', '4', '92', '93')):
            return True
    return False

def is_target_index(code):
    return enhance_stock_code(purified_code(code), 'index', True) == code

def get_derive_by_code(code, type = 'index'):
    if type != 'index':
        return -1
    if code.startswith('H'):
        return 1
    if code.startswith('000'):
        return 1
    if code.startswith("9"):
        return 2
    if code.startswith("399"):
        return 0
    logger.info(f"Index [{code}] with type [{type}] has no corresponding market source.")
    return -1


def purified_code(code):
    return code.split('.')[0]


def should_print(task_name, gap):
    current_time = get_time()
    last_time = last_print_times.get(task_name, 0)
    if current_time - last_time >= gap:
        last_print_times[task_name] = current_time
        return True
    return False


def is_market_closing():
    # Get current time
    now = get_datetime()
    # 0-4 represents Monday to Friday
    if now.weekday() >= 5:
        return True
    if MARKET_TIMES['morning_open'] <= now.time() <= MARKET_TIMES['morning_close']:
        return False
    if MARKET_TIMES['afternoon_open'] <= now.time() <= MARKET_TIMES['afternoon_close']:
        return False
    return True

def is_market_after_buffer():
    now = get_datetime()
    if now.weekday() >= 5:
        return False
    return (MARKET_TIMES['buffer_morning_open'] <= now.time() <= MARKET_TIMES['morning_close']) or (MARKET_TIMES['buffer_afternoon_open'] <= now.time() <= MARKET_TIMES['afternoon_close'])

def is_normal_trading_hours():
    now = get_datetime()
    if now.weekday() >= 5:
        return False
    return (MARKET_TIMES['buffer_morning_open'] <= now.time() <= MARKET_TIMES['morning_close']) or (MARKET_TIMES['buffer_afternoon_open'] <= now.time() <= MARKET_TIMES['call_auction'])


def is_going_to_close():
    now = get_datetime()
    return now.time() >= MARKET_TIMES['gonna_close']