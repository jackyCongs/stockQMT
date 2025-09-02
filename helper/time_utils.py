# coding=utf-8

from datetime import datetime, timedelta
import time
import threading

# 时间锚点：启动时的基准时间
_initial_monotonic = time.monotonic()  # 高效单调时间
_initial_time = time.time()  # 对应time.time()的初始值
_initial_datetime = datetime.now()  # 对应datetime.now()的初始值

# 缓存变量
_cached_timestamp = _initial_time  # 对应time.time()的缓存
_cached_datetime = _initial_datetime  # 对应datetime.now()的缓存

# 更新间隔（秒）
UPDATE_INTERVAL = 0.01  # 10ms更新一次缓存
CALIBRATE_INTERVAL = 10  # 每10秒校准一次（调整为10秒）


def _time_updater():
    """后台线程：更新两个缓存的时间值，每10秒校准一次"""
    global _cached_timestamp, _cached_datetime
    global _initial_monotonic, _initial_time, _initial_datetime

    while True:
        # 计算当前单调时间与初始值的偏移量
        current_monotonic = time.monotonic()
        offset_seconds = current_monotonic - _initial_monotonic

        # 更新time.time()对应的缓存
        _cached_timestamp = _initial_time + offset_seconds

        # 更新datetime.now()对应的缓存
        _cached_datetime = _initial_datetime + timedelta(seconds=offset_seconds)

        # 每10秒校准一次，同步系统时间
        if offset_seconds >= CALIBRATE_INTERVAL:
            _initial_monotonic = current_monotonic
            _initial_time = time.time()  # 校准系统时间戳
            _initial_datetime = datetime.now()  # 校准datetime

            # 重置偏移量计算起点
            offset_seconds = 0

        time.sleep(UPDATE_INTERVAL)


# 启动后台更新线程
threading.Thread(target=_time_updater, daemon=True, name="TimeUpdater").start()


def get_time():
    """
    替代time.time()的高效实现
    返回float类型的时间戳，与time.time()格式完全一致
    """
    return _cached_timestamp


def get_datetime():
    """
    替代datetime.now()的高效实现
    返回datetime.datetime对象，与datetime.now()格式完全一致
    """
    return _cached_datetime
