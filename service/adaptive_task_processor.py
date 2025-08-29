# coding=utf-8

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import PriorityQueue
import psutil
import math


class AdaptiveTaskProcessor:
    def __init__(self, base_workers=100, max_pending_tasks=400):
        # 硬件基础参数（10核20线程）
        self.physical_cores = 10
        self.logical_cores = 20

        # 线程池配置（IO密集型默认100，是逻辑核心的5倍）
        self.base_workers = base_workers
        self.max_workers = self.logical_cores * 10  # 最大可扩容至200

        # 带优先级的任务队列（0为最高优先级）
        self.task_queue = PriorityQueue(maxsize=max_pending_tasks)
        self.thread_name_prefix = "TaskWorker"

        # 线程池初始化
        self.executor = ThreadPoolExecutor(
            max_workers=self.base_workers,
            thread_name_prefix=self.thread_name_prefix
        )

        # 状态监控变量
        self.running = True
        self.task_counter = 0

        # 启动任务调度器和监控器
        self._start_scheduler()
        self._start_monitor()

    def _start_scheduler(self):
        """任务调度器：从队列取任务提交给线程池，支持动态调整线程数"""
        def schedule():
            while self.running:
                # 动态调整线程池大小（根据CPU负载）
                cpu_usage = psutil.cpu_percent(interval=0.1)
                if cpu_usage < 30 and self.executor._max_workers < self.max_workers:
                    # CPU空闲时扩容（每次增加10）
                    new_workers = min(self.executor._max_workers + 10, self.max_workers)
                    self.executor._max_workers = new_workers
                elif cpu_usage > 70 and self.executor._max_workers > self.base_workers:
                    # CPU负载高时缩容（每次减少10）
                    new_workers = max(self.executor._max_workers - 10, self.base_workers)
                    self.executor._max_workers = new_workers

                # 从队列取任务执行
                try:
                    # 1秒内取不到任务则超时，避免阻塞
                    priority, task, args, kwargs = self.task_queue.get(timeout=1)
                    self.executor.submit(self._wrap_task, task, args, kwargs)
                    self.task_queue.task_done()
                except:
                    continue

        threading.Thread(target=schedule, daemon=True, name="TaskScheduler").start()

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
                print(f"[监控] TPS: {tps}/秒 | 活跃线程: {threading.active_count()} | "
                      f"队列等待: {self.task_queue.qsize()} | "
                      f"CPU使用率: {psutil.cpu_percent()}% | "
                      f"线程池大小: {self.executor._max_workers}")
                time.sleep(1)

        threading.Thread(target=monitor, daemon=True, name="SystemMonitor").start()

    def _wrap_task(self, task, args, kwargs):
        """任务包装器：统计任务数和处理时间"""
        try:
            start = time.time()
            task(*args, **kwargs)
            # 可选：记录慢任务（处理时间>100ms）
            if time.time() - start > 0.1:
                print(f"[警告] 慢任务耗时: {time.time( ) -start:.3f}秒")
        except Exception as e:
            print(f"任务执行失败: {e}")
        finally:
            self.task_counter += 1

    def submit_task(self, task, *args, priority=1, **kwargs):
        """提交任务（支持优先级，0最高）"""
        try:
            # 非阻塞提交，队列满时返回False（可根据需求改为block=True）
            self.task_queue.put((priority, task, args, kwargs), block=False)
            return True
        except Exception as e:
            print(f"任务队列已满，丢弃任务: {e}")
            return False

    def shutdown(self):
        """优雅关闭资源"""
        self.running = False
        self.task_queue.join()
        self.executor.shutdown(wait=True)
        print("所有任务处理完成，已关闭")