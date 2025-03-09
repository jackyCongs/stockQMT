# coding=utf-8
from datetime import datetime
from xtquant import xtdata
import time

# 传参毫秒级
def transfer_date(timestamp):
    # 转换为秒
    timestamp_sec = timestamp / 1000
    # 再转换为日期
    dt = datetime.fromtimestamp(timestamp_sec)
    # 输出: 2025-03-07
    return dt.strftime('%Y-%m-%d')

# 今天是否是交易日
def is_today_trading():
    current_date = datetime.now().strftime("%Y%m%d")
    trading_dates = xtdata.get_trading_dates("SZ", current_date, current_date)
    return len(trading_dates) >= 1

def get_current_second():
    return int(time.time())

def get_current_millisecond():
    return int(time.time() * 1000)