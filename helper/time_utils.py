# coding=utf-8

from datetime import datetime, timedelta
import time
import threading

# 时间快照列表：仅在尾部追加新快照，读取时只取最后一个（最新的）
_time_snapshots = [(time.time(), datetime.now())]

# 配置参数
UPDATE_INTERVAL = 0.02  # 20ms更新一次快照 → 最大时间延迟为10ms（保证实时性）
MAX_SNAPSHOTS = 1000  # 最多保留1000个快照（仅用于控制内存占用，不影响实时性）
KEEP_LATEST = 100  # 清理时保留最近100个快照（足够覆盖多个更新周期）


def get_time():
    """返回最新快照的时间戳，延迟≤10ms（实时性保证）"""
    #return _time_snapshots[-1][0]
    return time.time()


def get_datetime():
    """返回最新快照的datetime对象，延迟≤10ms（实时性保证）"""
    #return _time_snapshots[-1][1]
    return datetime.now()
