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
    
    # Singleton pattern
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:  # Thread lock to ensure thread safety
                if not cls._instance:
                    cls._instance = super(AdaptiveTaskProcessor, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        # 6 performance cores + 8 efficiency cores
        self.physical_cores = 14
        self.logical_cores = 20

        # Thread pool config (IO-bound defaults to 100, which is 5x logical cores)
        self.base_workers = self.physical_cores * 4
        # Thread pool config: 20 core threads (close to logical core count), max 50 (to prevent excessive context switching)
        self.executor = ThreadPoolExecutor(
            max_workers=self.base_workers,
            thread_name_prefix="TaskWorker"
        )

        self.running = True
        self.task_counter = 0

        # Start system status monitor
        self._start_monitor()
        self._initialized = True

    def _start_monitor(self):
        """Real-time monitoring of system telemetry and task execution throughput"""
        def monitor():
            last_count = 0
            while self.running:
                # Calculate tasks processed per second (TPS)
                current_count = self.task_counter
                tps = current_count - last_count
                last_count = current_count

                # Log system health information
                logging.info(f"[Monitor] | {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}|"
                      f" TPS: {tps}/sec | Active Threads: {threading.active_count()} | "
                      f"CPU Usage: {psutil.cpu_percent()}% | "
                      f"Memory Usage: {psutil.virtual_memory().percent}% | "
                      f"Thread Pool Size: {self.executor._max_workers}")
                if threading.active_count() > self.executor._max_workers + 20:
                    logging.info(f"[CRITICAL WARNING] Thread pool is potentially overloaded! Current active threads: {threading.active_count()}, consider increasing base_workers.")
                time.sleep(20)

        threading.Thread(target=monitor, daemon=True, name="SystemMonitor").start()

    def _wrap_task(self, task, args, kwargs, submit_time):
        """Task wrapper: tracks task counter and metrics"""
        try:
            start_time = get_time()
            task(*args, **kwargs)
            end_time = get_time()
            # logging.info(
            #     f"Task duration: {(end_time - start_time) * 1000:.1f}ms | Total queue-to-completion time: {(end_time - submit_time) * 1000:.1f}ms")

            # Optional: Log slow tasks (execution duration > 100ms)
            if end_time - start_time > 1:
                logging.info(f"[WARNING] {task.__name__} [Slow task execution time]: {end_time - start_time:.3f}s [Total queue-to-completion time]: {end_time - submit_time:.3f}s")
        except Exception as e:
            logging.error(f"Task execution failed: {e}", exc_info=True)
        finally:
            self.task_counter += 1

    def submit_task(self, task, *args, **kwargs):
        """Submit a task to the execution pool"""
        submit_time = get_time()
        try:
            # Non-blocking submit. Returns False if queue is full (can configure block=True if needed)
            self.executor.submit(self._wrap_task, task, args, kwargs, submit_time)
            return True
        except Exception as e:
            logging.info(f"Failed to submit task thread: {e}")
            return False

    def shutdown(self):
        """Gracefully shutdown and release resources"""
        self.running = False
        self.executor.shutdown(wait=True)
        logging.info("All tasks processed. Engine shutdown complete.")