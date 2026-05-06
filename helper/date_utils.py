# coding=utf-8
from datetime import datetime, timedelta
from xtquant import xtdata
import time
from helper.time_utils import get_time, get_datetime


# 传参毫秒级
def transfer_date(timestamp):
    # 转换为秒
    timestamp_sec = timestamp / 1000
    # 再转换为日期
    dt = datetime.fromtimestamp(timestamp_sec)
    # 输出: 2025-03-07
    return dt.strftime('%Y-%m-%d')

def transfer_time(timestamp):
    # 转换为秒
    timestamp_sec = timestamp / 1000
    # 转换为日期时间对象
    dt = datetime.fromtimestamp(timestamp_sec)
    # 输出示例: 14:30:05
    return dt.strftime('%H:%M:%S')

# 今天是否是交易日
def is_today_trading():
    current_date = get_datetime().strftime("%Y%m%d")
    trading_dates = xtdata.get_trading_dates("SZ", current_date, current_date)
    return len(trading_dates) >= 1

def get_current_second():
    return int(get_time())

def get_current_millisecond():
    return int(get_time() * 1000)

def get_past_date_str(days_back: int = 15):
    """
    Returns a formatted string (YYYYMMDD) for a date in the past.
    :param days_back: Number of days to look back from today.
    :return: Formatted date string.
    """
    target_date = datetime.now() - timedelta(days=days_back)
    return target_date.strftime('%Y%m%d')

def format_date_fast(date_input) -> str:
    """
    极速格式化日期 (O(1) 复杂度)
    入参可以是字符串 '20260324' 或整数 20260324
    """
    # 强制转换为字符串，防止传入的是整数
    s = str(date_input)
    # 直接通过切片拼接: 前4位 + '-' + 中间2位 + '-' + 后2位
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"