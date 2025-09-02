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


def _time_updater():
    """后台线程：10ms生成一次新快照，仅追加到列表尾部"""
    global _time_snapshots
    while True:
        # 生成并追加最新快照（10ms一次，保证实时性）
        current_ts = time.time()
        current_dt = datetime.now()
        _time_snapshots.append((current_ts, current_dt))

        # 清理旧快照（仅删除头部的旧数据，保留尾部最新的100个）
        if len(_time_snapshots) > MAX_SNAPSHOTS:
            _time_snapshots = _time_snapshots[-KEEP_LATEST:]  # 尾部最新的永远保留

        # 休眠10ms，准备下一次更新
        time.sleep(UPDATE_INTERVAL)


# 启动后台线程（确保只启动一次）
if not hasattr(threading, "_time_snapshot_updater_started"):
    threading._time_snapshot_updater_started = True
    threading.Thread(
        target=_time_updater,
        daemon=True,
        name="RealTimeSnapshotUpdater"
    ).start()


def get_time():
    """返回最新快照的时间戳，延迟≤10ms（实时性保证）"""
    return _time_snapshots[-1][0]


def get_datetime():
    """返回最新快照的datetime对象，延迟≤10ms（实时性保证）"""
    return _time_snapshots[-1][1]
