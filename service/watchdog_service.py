# service/watchdog_service.py
import time
import threading
import logging
from helper import utils, notifier

logger = logging.getLogger(__name__)


class WatchdogService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # Singleton pattern: ensure a single global watchdog instance for easy accessibility
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(WatchdogService, cls).__new__(cls)
            return cls._instance

    def __init__(self):
        # Prevent double initialization
        if hasattr(self, 'initialized'):
            return
        self.initialized = True

        # Store monitors: { 'key': {'last_time': timestamp, 'timeout': threshold_seconds, 'desc': 'description'} }
        self.monitors = {}
        self.monitor_lock = threading.Lock()
        self.running = False

    def start(self):
        """Start background monitoring thread"""
        if self.running:
            return
        self.running = True
        thread = threading.Thread(target=self._loop_check, daemon=True, name="GlobalWatchdog")
        thread.start()
        logger.info("🐶 Global Watchdog service started.")

    def register(self, key, timeout_seconds=180, description="未知任务"):
        """
        Register a monitoring item.
        :param key: Unique identifier (e.g. 'strategy1_main' or 'index_510300')
        :param timeout_seconds: Timeout threshold in seconds
        :param description: Descriptive string displayed in alerts
        """
        with self.monitor_lock:
            self.monitors[key] = {
                'last_time': time.time(),
                'timeout': timeout_seconds,
                'desc': description
            }
        logger.info(f"Watchdog added monitor: [{key}] - {description}")

    def feed(self, key):
        """
        Feed the dog: Update heartbeat timestamp for a specific key.
        """
        # Non-locking reads for performance. Write operations are atomic/low overhead.
        # Use lock for strict rigor, but dict update is thread-safe.
        if key in self.monitors:
            self.monitors[key]['last_time'] = time.time()

    def _loop_check(self):
        while self.running:
            try:
                print("Watchdog patrolling...")
                time.sleep(60)  # Check once per minute
                # 1. Completely skip checks outside normal trading hours
                if utils.is_market_closing():
                    # Automatically refresh heartbeats outside trading hours to prevent false positives at market open
                    with self.monitor_lock:
                        now = time.time()
                        for k in self.monitors:
                            self.monitors[k]['last_time'] = now
                    continue

                # 2. Verify all monitored items
                alerts = []
                with self.monitor_lock:
                    now = time.time()
                    for key, info in self.monitors.items():
                        # Calculate silent duration
                        silence_duration = now - info['last_time']
                        if silence_duration > info['timeout']:
                            alerts.append(f"🔴 {info['desc']} (ID: {key})\n   Lost contact for {int(silence_duration)} seconds\n\n")
                # 3. Dispatch unified alerts if anomalies are found
                if alerts:
                    alert_content = "\n\n".join(alerts)
                    logger.error(f"Watchdog detected anomaly:\n{alert_content}")
                    notifier.send_telegram_alert(
                        "Watchdog Anomaly Detected",
                        f"The following tasks have failed to refresh their heartbeats. Please check immediately!\n\n{alert_content}"
                    )
            except Exception as e:
                logger.error(f"Watchdog thread exception: {e}")