# coding=utf-8
from datetime import datetime, timedelta
from xtquant import xtdata
import time
from helper.time_utils import get_time, get_datetime


# Accepts timestamp in milliseconds
def transfer_date(timestamp):
    # Convert to seconds
    timestamp_sec = timestamp / 1000
    # Convert to date
    dt = datetime.fromtimestamp(timestamp_sec)
    # Output: 2025-03-07
    return dt.strftime('%Y-%m-%d')

def transfer_time(timestamp):
    # Convert to seconds
    timestamp_sec = timestamp / 1000
    # Convert to datetime object
    dt = datetime.fromtimestamp(timestamp_sec)
    # Output example: 14:30:05
    return dt.strftime('%H:%M:%S')

# Check if today is a trading day
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
    High-performance date formatter (O(1) complexity).
    Input can be string '20260324' or integer 20260324.
    """
    # Force convert to string to handle integer inputs
    s = str(date_input)
    # Slice and concatenate: YYYY + '-' + MM + '-' + DD
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"