# service/watchdog_service.py
import time
import threading
import logging
from helper import utils, notifier  # 假设你的工具类在这里

logger = logging.getLogger(__name__)


class WatchdogService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # 单例模式：确保全局只有一个看门狗，方便任何地方调用
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(WatchdogService, cls).__new__(cls)
            return cls._instance

    def __init__(self):
        # 防止重复初始化
        if hasattr(self, 'initialized'):
            return
        self.initialized = True

        # 存储监控项：{ '监控键名': {'last_time': 时间戳, 'timeout': 超时阈值, 'desc': '描述'} }
        self.monitors = {}
        self.monitor_lock = threading.Lock()
        self.running = False

    def start(self):
        """启动后台监控线程"""
        if self.running:
            return
        self.running = True
        thread = threading.Thread(target=self._loop_check, daemon=True, name="GlobalWatchdog")
        thread.start()
        logger.info("🐶 全局看门狗服务已启动")

    def register(self, key, timeout_seconds=180, description="未知任务"):
        """
        注册一个监控项
        :param key: 唯一标识，如 'strategy1_main' 或 'index_510300'
        :param timeout_seconds: 超时时间（秒）
        :param description: 报警时显示的中文描述
        """
        with self.monitor_lock:
            self.monitors[key] = {
                'last_time': time.time(),
                'timeout': timeout_seconds,
                'desc': description
            }
        logger.info(f"看门狗新增监控: [{key}] - {description}")

    def feed(self, key):
        """
        喂狗：更新某个键的心跳时间
        """
        # 为了性能，这里不加锁读取，只有写入时其实是原子的，或者是快速操作
        # 如果追求极致严谨可以用锁，但 update 是线程安全的
        if key in self.monitors:
            self.monitors[key]['last_time'] = time.time()

    def _loop_check(self):
        while self.running:
            try:
                print("看门狗巡查中....")
                time.sleep(60)  # 每分钟检查一次
                # 1. 如果是非交易时间，完全跳过检查
                if utils.is_market_closing():
                    # 在非交易时间，自动刷新所有心跳，防止开盘瞬间误报
                    with self.monitor_lock:
                        now = time.time()
                        for k in self.monitors:
                            self.monitors[k]['last_time'] = now
                    continue

                # 2. 检查所有监控项
                alerts = []
                with self.monitor_lock:
                    now = time.time()
                    for key, info in self.monitors.items():
                        # 计算静默时间
                        silence_duration = now - info['last_time']
                        if silence_duration > info['timeout']:
                            alerts.append(f"🔴 {info['desc']} (ID: {key})\n   已失联 {int(silence_duration)} 秒\n\n")
                # 3. 如果有异常，统一发送报警
                if alerts:
                    alert_content = "\n\n".join(alerts)
                    logger.error(f"看门狗发现异常:\n{alert_content}")
                    notifier.send_telegram_alert(
                        "看门狗发现异常",
                        f"以下任务长期未更新心跳，请检查！\n\n{alert_content}"
                    )
            except Exception as e:
                logger.error(f"看门狗线程出错: {e}")