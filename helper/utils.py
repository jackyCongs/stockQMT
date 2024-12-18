# coding=utf-8

import time
import logging
from datetime import datetime, time as d_time

last_print_time = time.time()
logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)


def enhance_stock_code(code, type='stock'):
    if type == 'index':
        if code.startswith('000'):
            return f"{code}.SH"
        if code.startswith('399'):
            return f"{code}.SZ"
        if code.startswith("9"):
            return code
        logger.info(f"当前 code: {code}-{type}, 无对应来源")
        return code
    # 上证
    if len(code) == 6 and (code.startswith('6') or code.startswith('900') or code.startswith('5')):
        return f"{code}.SH"
    # 深证
    elif len(code) == 6 and (code.startswith('0') or code.startswith('3') or code.startswith('158')
                             or code.startswith('159') or code.startswith('16')):
        return f"{code}.SZ"
    logger.info(f"当前 code: {code}-{type}, 无对应来源")
    return code


def get_derive_by_code(code, type = 'index'):
    if type != 'index':
        return -1
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


def should_print(gap):
    current_time = time.time()
    global last_print_time
    if current_time - last_print_time >= gap:
        last_print_time = current_time
        return True
    return False


def is_market_closing():
    # 获取当前时间
    now = datetime.now()
    morning_open_time = d_time(9, 30)
    morning_close_time = d_time(11, 30)
    afternoon_open_time = d_time(13, 0)
    afternoon_close_time = d_time(15, 0)
    # 0-4 表示周一至周五
    if now.weekday() >= 5:
        return True
    if morning_open_time <= now.time() <= morning_close_time:
        return False
    if afternoon_open_time <= now.time() <= afternoon_close_time:
        return False
    return True
