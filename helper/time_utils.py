# coding=utf-8

from datetime import datetime, timedelta
import time
import threading

# Time snapshot list: new snapshots appended at the tail; reads only the last one (latest)
_time_snapshots = [(time.time(), datetime.now())]

# Configuration parameters
UPDATE_INTERVAL = 0.02  # Updates snapshot every 20ms -> max delay is 10ms (guarantees real-time precision)
MAX_SNAPSHOTS = 1000  # Maximum of 1000 snapshots retained (used to control memory usage, does not affect latency)
KEEP_LATEST = 100  # Retain the last 100 snapshots during cleanup (sufficient to cover multiple update cycles)


def get_time():
    """Returns the latest snapshot timestamp with delay <= 10ms (guarantees real-time precision)"""
    #return _time_snapshots[-1][0]
    return time.time()


def get_datetime():
    """Returns the latest snapshot datetime object with delay <= 10ms (guarantees real-time precision)"""
    #return _time_snapshots[-1][1]
    return datetime.now()
