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
            logger.warning(f"当前 code: {code}-{type}, 无对应来源")
        return code
    # 上证
    if len(code) == 6 and (code.startswith('6') or code.startswith('900') or code.startswith('5')):
        return f"{code}.SH"
    # 深证
    elif len(code) == 6 and (code.startswith('00') or code.startswith('3') or code.startswith('158')
                             or code.startswith('159') or code.startswith('16')):
        return f"{code}.SZ"
    # 北交所
    elif len(code) == 6 and code.startswith(('8', '4', '92', '93')):
        return f"{code}.BJ"
    # 债券等其他品种（国债、企债、转债等）
    elif len(code) == 6 and code.startswith(('11', '12', '13', '14', '17', '18', '19', '01', '02', '10')):
        # 简单区分，123/127/128/112 通常是深交所，其余大概率是上交所
        if code.startswith(('123', '127', '128', '112')):
            return f"{code}.SZ"
        return f"{code}.SH"
    logger.warning(f"当前 code: {code}-{type}, 无对应来源")
    return code

def is_normal_a_share(code):
    """
    判断一个成分股是否是标准的 A 股正股。
    排除债券、黄金、港股、美股、基金等。
    标准A股前缀:
    - 上海: 60, 68
    - 深圳: 00, 30
    - 北交所: 8, 4, 92, 93
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
    logger.info(f"当前指数 code: {code}-{type}, 无对应来源")
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
    # 获取当前时间
    now = get_datetime()
    # 0-4 表示周一至周五
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