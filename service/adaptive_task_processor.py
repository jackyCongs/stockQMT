# coding=utf-8

import time
import threading
from concurrent.futures import ThreadPoolExecutor
import psutil
import logging
from helper.time_utils import get_time, get_datetime


class AdaptiveTaskProcessor:
    _instance = None
    _lock = threading.Lock()
    # 单例模式
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:  # 加锁保证多线程安全
                if not cls._instance:
                    cls._instance = super(AdaptiveTaskProcessor, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        # 6个大核 + 8个小核
        self.physical_cores = 14
        self.logical_cores = 20

        # 线程池配置（IO密集型默认100，是逻辑核心的5倍）
        self.base_workers = self.physical_cores * 4
        # 线程池配置：核心线程20（接近逻辑核心数），最大50（避免过多切换）
        self.executor = ThreadPoolExecutor(
            max_workers=self.base_workers,
            thread_name_prefix="TaskWorker"
        )

        self.running = True
        self.task_counter = 0

        # 启动监控器
        self._start_monitor()
        self._initialized = True

    def _start_monitor(self):
        """实时监控系统状态和任务处理效率"""
        def monitor():
            last_count = 0
            while self.running:
                # 计算每秒处理任务数
                current_count = self.task_counter
                tps = current_count - last_count
                last_count = current_count

                # 打印监控信息
                logging.info(f"[监控] | {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}|"
                      f" TPS: {tps}/秒 | 活跃线程: {threading.active_count()} | "
                      f"CPU使用率: {psutil.cpu_percent()}% | "
                      f"内存使用率: {psutil.virtual_memory().percent}% | "
                      f"线程池大小: {self.executor._max_workers}")
                if threading.active_count() > self.executor._max_workers + 20:
                    logging.info(f"[严重警告] 线程池疑似满载！当前总线程: {threading.active_count()}，请考虑增加 base_workers")
                time.sleep(20)

        threading.Thread(target=monitor, daemon=True, name="SystemMonitor").start()

    def _wrap_task(self, task, args, kwargs, submit_time):
        """任务包装器：统计任务数和处理时间"""
        try:
            start_time = get_time()
            task(*args, **kwargs)
            end_time = get_time()
            # logging.info(
            #     f"任务执行耗时: {(end_time - start_time) * 1000:.1f}ms | 从提交到完成总耗时: {(end_time - submit_time) * 1000:.1f}ms")

            # 可选：记录慢任务（处理时间>100ms）
            if end_time - start_time > 1:
                logging.info(f"[警告] {task.__name__} [慢任务耗时]: {end_time -start_time:.3f}秒 [提交耗时]: {end_time - submit_time:.3f}")
        except Exception as e:
            logging.error(f"任务执行失败: {e}", exc_info=True)
        finally:
            self.task_counter += 1

    def submit_task(self, task, *args, **kwargs):
        """提交任务（支持优先级，0最高）"""
        submit_time = get_time()
        try:
            # 非阻塞提交，队列满时返回False（可根据需求改为block=True）
            self.executor.submit(self._wrap_task, task, args, kwargs, submit_time)
            return True
        except Exception as e:
            logging.info(f"提交线程失败 {e}")
            return False

    def shutdown(self):
        """优雅关闭资源"""
        self.running = False
        self.executor.shutdown(wait=True)
        logging.info("所有任务处理完成，已关闭")