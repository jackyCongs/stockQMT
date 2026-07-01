# coding=utf-8
import logging
import helper.data_loader as data_loader
from helper import utils, spider, date_utils
from helper.time_utils import get_time, get_datetime

logger = logging.getLogger(__name__)

class StockNode:
    def __init__(self, code, name, quantity, price, premium, real_premium, appraisal, history_penalty_rate, overheating_penalty, update_time):
        self.code = code
        self.name = name
        self.quantity = quantity
        self.price = price
        self.premium = premium
        self.real_premium = real_premium
        self.appraisal = appraisal
        self.history_penalty_rate = history_penalty_rate
        self.overheating_penalty = overheating_penalty
        self.update_time = update_time
        self.prev = None
        self.next = None


class StockQueue:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0
        self.code_map = {}

    def upsert_stock(self, code, name, quantity, price, premium, real_premium, appraisal, history_penalty_rate, overheating_penalty, update_time):
        # 统一处理新增和更新
        if code in self.code_map:
            self._remove_node(self.code_map[code])
            del self.code_map[code]

        new_node = StockNode(code, name, quantity, price, premium, real_premium, appraisal, history_penalty_rate, overheating_penalty, update_time)
        self.code_map[code] = new_node

        # 处理空队列
        if not self.head:
            self.head = self.tail = new_node
            self.size = 1
            return True

        # 寻找插入位置
        current = self.head
        while current and current.premium >= premium:
            current = current.next

        # 插入头部
        if current == self.head:
            new_node.next = self.head
            self.head.prev = new_node
            self.head = new_node
        # 插入尾部
        elif not current:
            new_node.prev = self.tail
            self.tail.next = new_node
            self.tail = new_node
        # 中间插入
        else:
            prev_node = current.prev
            prev_node.next = new_node
            new_node.prev = prev_node
            new_node.next = current
            current.prev = new_node

        # self.size += 1
        return True

    def remove_stock(self, code):
        # 安全删除处理
        if code not in self.code_map:
            return False

        node = self.code_map[code]
        self._remove_node(node)
        del self.code_map[code]
        return True

    def _remove_node(self, node):
        if node.prev:
            node.prev.next = node.next
        else:
            self.head = node.next

        if node.next:
            node.next.prev = node.prev
        else:
            self.tail = node.prev

        self.size -= 1

    def print_queue(self):
        current = self.head
        print()
        while current:
            logger.info(f"[{current.code}] {current.name} 估值: {round(current.appraisal, 5)} premium: {round(current.premium, 3)}% real_premium: {round(current.real_premium, 3)}% 价格: {round(current.price, 3)}, 数量: {current.quantity}, 总价: {round(current.quantity*current.price*100, 2)}, 历史惩罚: {current.history_penalty_rate}, 过热惩罚: {current.overheating_penalty}, 更新时间: {date_utils.transfer_time(current.update_time)}")
            current = current.next


class PremiumStrategyManager:
    """
    策略管理器
    职责：持有买卖队列，根据行情数据维护队列状态，执行策略逻辑
    """
    def __init__(self, base_premium_threshold, min_bid_money, max_bid_money):
        # 策略内部维护两个队列，不再需要外部传入,每个指数单独是一组
        self.sell_queue = StockQueue()
        self.buy_queue = StockQueue()

        self.base_premium_threshold = base_premium_threshold
        self.min_bid_money = min_bid_money
        self.max_bid_money = max_bid_money

    def update(self, stock_code, stock_info, index_info, realtime_iopv_info):
        # ------------------------------------------------------------------
        # 1. 维护委卖队列 (Sell Queue)
        # ------------------------------------------------------------------
        appraisal = realtime_iopv_info['current']
        current_hold_val = float(stock_info['hold_num']) * stock_info['askPrice'][0] * 100
        should_remove_sell = True  # 默认标记为移除，除非满足特定条件
        index_unused_money_capacity = self.max_bid_money * 2 - index_info['index_total_market_value']
        # 逻辑：总持仓金额未超限、指数总持仓未超限
        if (current_hold_val + self.min_bid_money < self.max_bid_money) and (index_unused_money_capacity > self.min_bid_money):
            # 卖一价有效且小于估值
            if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0] <= appraisal:
                # 动态计算门槛
                overheating_penalty = float(data_loader.get_overheating_penalty(realtime_iopv_info['increase_rate']))
                history_penalty_rate = float(index_info['penalty_rate'])
                premium_threshold = float(self.base_premium_threshold) + overheating_penalty + history_penalty_rate

                first_premium = round((appraisal - stock_info['askPrice'][0]) / appraisal * 100, 4)
                # 满足折价率要求 且 挂单金额足够
                if first_premium > 0 and stock_info["askVol"][0] * stock_info["askPrice"][0] * 100 > self.min_bid_money:
                    # 计算权重并更新队列
                    adjusted_premium = first_premium - premium_threshold
                    self.sell_queue.upsert_stock(
                        stock_code,
                        stock_info["name"],
                        stock_info["askVol"][0],
                        stock_info["askPrice"][0],
                        adjusted_premium,
                        first_premium, # real_premium
                        appraisal,
                        history_penalty_rate,
                        overheating_penalty,
                        date_utils.get_current_millisecond()
                    )
                    should_remove_sell = False

        if should_remove_sell:
            self.sell_queue.remove_stock(stock_code)

        # ------------------------------------------------------------------
        # 2. 维护委买队列 (Buy Queue)
        # ------------------------------------------------------------------
        buy_premium = round((stock_info['bidPrice'][0] - appraisal) / appraisal * 100, 4)
        buy_premium_threshold = float(data_loader.get_sell_premium(realtime_iopv_info['increase_rate']))

        # 逻辑：买一存在 > 0，有持仓可用，买一金额 > 200元
        if (len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] > 0 and
                stock_info['hold_num'] > 0 and
                stock_info["bidVol"][0] * stock_info["bidPrice"][0] * 100 > 200):
            self.buy_queue.upsert_stock(
                stock_code,
                stock_info["name"],
                stock_info["bidVol"][0],
                stock_info["bidPrice"][0],
                buy_premium - buy_premium_threshold,
                buy_premium,
                appraisal,
                0, 0,
                date_utils.get_current_millisecond()
            )
        else:
            self.buy_queue.remove_stock(stock_code)
        # ------------------------------------------------------------------
        # 3. 日志与状态打印
        # ------------------------------------------------------------------
        self._print_status(stock_code)

        # 快收盘的前几分钟，开始每10秒展示实际估值
        if utils.is_going_to_close() and utils.should_print("stock_queue", 10):
            self.buy_queue.print_queue()

    def _print_status(self, stock_code):
        is_close = utils.is_going_to_close()
        should_print_normal = utils.should_print("stock_queue", 60)
        should_print_close = is_close and utils.should_print("stock_queue_close", 10)
        if should_print_normal or should_print_close:
            logger.info("********************** 全局队列数据情况 start **********************")
            logger.info(f"当前触发股票: {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            sell_logs = ["\r\n--- 全局 Sell 队列  ---"]
            curr = self.sell_queue.head
            sell_count = 0
            while curr and sell_count < 5:
                real_premium_pct = round((curr.appraisal - curr.price) / curr.price * 100, 4)
                amount = round(curr.price * curr.quantity * 100, 2)
                update_time_str = date_utils.transfer_time(curr.update_time)
                sell_logs.append(
                    f"[{curr.code}] {curr.name}, 估值: {curr.appraisal}, "
                    f"卖一报价: {round(curr.price, 4)}, 折价率: {real_premium_pct}%, "
                    f"premium权重: {curr.premium}, 更新时间: {update_time_str}, 金额 {amount}, 历史惩罚: {curr.history_penalty_rate}, 过热惩罚: {curr.overheating_penalty}"
                )
                curr = curr.next
                sell_count += 1

            buy_logs = ["\r\n--- 全局 Buy 队列 ---"]
            curr = self.buy_queue.head
            buy_count = 0
            while curr:
                real_premium_pct = round((curr.price - curr.appraisal) / curr.price * 100, 4)
                amount = round(curr.price * curr.quantity * 100, 2)
                update_time_str = date_utils.transfer_time(curr.update_time)
                buy_logs.append(
                    f"[{curr.code}] {curr.name}, 估值: {curr.appraisal}, "
                    f"买一报价: {round(curr.price, 4)}, 折价率: {real_premium_pct}%, "
                    f"premium权重: {curr.premium}, 更新时间: {update_time_str}, 金额 {amount}"
                )
                curr = curr.next
                buy_count += 1

            if sell_count > 0:
                logger.info("\n".join(sell_logs))
            if buy_count > 0:
                logger.info("\n".join(buy_logs))
            logger.info("********************** 全局队列数据情况 end **********************")