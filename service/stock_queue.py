# coding=utf-8
import logging
import helper.data_loader as data_loader
from helper import utils, spider, date_utils
from helper.time_utils import get_time, get_datetime

logger = logging.getLogger(__name__)

class StockNode:
    def __init__(self, code, name, quantity, price, premium, real_premium, appraisal, update_time):
        self.code = code
        self.name = name
        self.quantity = quantity
        self.price = price
        self.premium = premium
        self.real_premium = real_premium
        self.appraisal = appraisal
        self.update_time = update_time
        self.prev = None
        self.next = None


class StockQueue:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0
        self.code_map = {}

    def upsert_stock(self, code, name, quantity, price, premium, real_premium, appraisal, update_time):
        # 统一处理新增和更新
        if code in self.code_map:
            self._remove_node(self.code_map[code])
            del self.code_map[code]

        new_node = StockNode(code, name, quantity, price, premium, real_premium, appraisal, update_time)
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
            logger.info(f"[{current.code}] {current.name} 估值: {round(current.appraisal, 5)} premium: {round(current.premium, 3)}% real_premium: {round(current.real_premium, 3)}% 价格: {round(current.price, 3)} 数量 {current.quantity} 总价 {round(current.quantity*current.price*100, 2)}")
            current = current.next


class PremiumStrategyManager:
    """
    策略管理器
    职责：持有买卖队列，根据行情数据维护队列状态，执行策略逻辑
    """
    def __init__(self, base_premium_threshold, min_bid_money, max_bid_money):
        # 策略内部维护两个队列，不再需要外部传入,每个指数单独是一组
        self.sell_queue = {}#StockQueue()
        self.buy_queue = {}#StockQueue()

        self.base_premium_threshold = base_premium_threshold
        self.min_bid_money = min_bid_money
        self.max_bid_money = max_bid_money

    def update(self, appraisal, stock_code, index_code, stock_info, index_info):
        # ------------------------------------------------------------------
        # 1. 维护委卖队列 (Sell Queue)
        # ------------------------------------------------------------------
        # 计算当前持仓市值
        # 以指数为组，以组为单位维护住queue
        if index_code not in self.sell_queue:
            self.sell_queue[index_code] = StockQueue()
            self.buy_queue[index_code] = StockQueue()
        current_hold_val = float(stock_info['hold_num']) * stock_info['askPrice'][0] * 100
        should_remove_sell = True  # 默认标记为移除，除非满足特定条件
        # 逻辑：总持仓金额未超限
        if current_hold_val + self.min_bid_money < self.max_bid_money:
            # 卖一价有效且小于估值
            if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0] <= appraisal:
                # 动态计算门槛
                premium_threshold = float(data_loader.get_premium(index_info['increase_rate'], self.base_premium_threshold))
                first_premium = round((appraisal - stock_info['askPrice'][0]) / appraisal * 100, 4)
                # 满足折价率要求 且 挂单金额足够
                if first_premium > premium_threshold and stock_info["askVol"][0] * stock_info["askPrice"][0] * 100 > self.min_bid_money:
                    # 计算权重并更新队列
                    adjusted_premium = first_premium - (premium_threshold - self.base_premium_threshold)
                    self.sell_queue[index_code].upsert_stock(
                        stock_code,
                        stock_info["name"],
                        stock_info["askVol"][0],
                        stock_info["askPrice"][0],
                        adjusted_premium,
                        first_premium, # real_premium
                        appraisal,
                        date_utils.get_current_millisecond()
                    )
                    should_remove_sell = False

        if should_remove_sell:
            self.sell_queue[index_code].remove_stock(stock_code)

        # ------------------------------------------------------------------
        # 2. 维护委买队列 (Buy Queue)
        # ------------------------------------------------------------------
        buy_premium = round((stock_info['bidPrice'][0] - appraisal) / appraisal * 100, 4)
        sell_premium_threshold = float(data_loader.get_sell_premium(index_info['increase_rate']))

        # 逻辑：买一存在 > 0，有持仓可用，买一金额 > 200元
        if (len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] > 0 and
                stock_info['hold_can_use_num'] > 0 and
                stock_info["bidVol"][0] * stock_info["bidPrice"][0] * 100 > 200):

            self.buy_queue[index_code].upsert_stock(
                stock_code,
                stock_info["name"],
                stock_info["bidVol"][0],
                stock_info["bidPrice"][0],
                buy_premium - sell_premium_threshold,
                buy_premium,
                appraisal,
                date_utils.get_current_millisecond()
            )
        else:
            self.buy_queue[index_code].remove_stock(stock_code)
        # ------------------------------------------------------------------
        # 3. 日志与状态打印
        # ------------------------------------------------------------------
        self._print_status(index_code, stock_code)

    def _print_status(self, index_code, stock_code):
        """
        私有方法：处理日志打印逻辑
        """
        # 每分钟 print 一次信息
        if utils.should_print(60):
            first_sell = self.sell_queue[index_code].head
            first_buy = self.buy_queue[index_code].head

            logger.info(f"\r\n{stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")

            if first_sell:
                logger.info(
                    f"-sell队列{first_sell.name}-{first_sell.code},估值: {first_sell.appraisal}, "
                    f"卖一报价: {round(first_sell.price, 4)}, "
                    f"折价率: {round((first_sell.appraisal - first_sell.price) / first_sell.price * 100, 4)}%, "
                    f"premium权重: {first_sell.premium}, "
                    f"金额 {round(first_sell.price * first_sell.quantity * 100, 2)}\r\n"
                )

            if first_buy:
                logger.info(
                    f"-buy队列{first_buy.name}-{first_buy.code},估值: {first_buy.appraisal}, "
                    f"买一报价: {round(first_buy.price, 4)}, "
                    f"折价率: {round((first_buy.price - first_buy.appraisal) / first_buy.price * 100, 4)}%, "
                    f"premium权重: {first_buy.premium}, "
                    f"金额 {round(first_buy.price * first_buy.quantity * 100, 2)}\r\n"
                )

            self.buy_queue[index_code].print_queue()

        # 快收盘的前几分钟，开始每10秒展示实际估值
        if utils.is_going_to_close() and utils.should_print(10):
            self.buy_queue[index_code].print_queue()