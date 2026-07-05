# coding=utf-8
import math

from xtquant.xttrader import XtQuantTrader
from xtquant import xtconstant

from helper import notifier
from service.trade_callback import TradeCallback
from service import account, adaptive_task_processor
import time
import threading
import logging
from helper.time_utils import get_datetime
import helper.data_loader as data_loader

logger = logging.getLogger(__name__)

class TraderService:
    def __init__(self, session_id, platform):
        self.platform = platform
        self.path = account.get_path(self.platform)
        self.session_id = session_id
        self.account = account.get_account(self.platform)
        self.xt_trader = self._create_trader()
        self._connect()
        self.locks = {}
        self.asset = self.xt_trader.query_stock_asset(self.account)

    def _create_trader(self):
        # 创建交易对象
        return XtQuantTrader(self.path, self.session_id)

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        lock_key = self.platform + "_" + stock_code
        if lock_key not in self.locks:
            self.locks[lock_key] = threading.Lock()
        return self.locks[lock_key]

    def _connect(self):
        # 建立交易连接
        self.xt_trader.register_callback(TradeCallback())
        self.xt_trader.start()
        while True:
            connect_result = self.xt_trader.connect()
            if connect_result == 0:
                logger.info(f"connect_result: {connect_result}, trade connected success")
                break
            else:
                logger.info(f"connect_result: {connect_result}, trade connected FAILED!!! retrying.......")
                time.sleep(1)

        # 订阅账户
        while True:
            subscribe_result = self.xt_trader.subscribe(self.account)
            if subscribe_result == 0:
                logger.info(f"subscribe_result: {subscribe_result}, account trading subscribed success")
                break
            else:
                logger.info(f"subscribe_result: {subscribe_result}, account trading subscribed FAILED!!! retrying.......")
                time.sleep(1)

    # 异步下单
    def async_buy(self, stock_code, bid_price, bid_num, strategy_name, inner_stock_infos, previous_hold_num):
        # 抢锁
        lock = self._get_lock(stock_code)
        lock.acquire()

        try:
            # 调用之前可能会有并发问题，在锁中需要再校验一词
            # if previous_hold_num != inner_stock_infos[stock_code]['hold_num']:
            #     return
            # inner_stock_infos[stock_code].update({'hold_num': previous_hold_num + bid_num})
            bid_num *= 100
            return self.xt_trader.order_stock(
                self.account, stock_code, xtconstant.STOCK_BUY, bid_num, xtconstant.FIX_PRICE, bid_price,
                strategy_name
            )
        except Exception as e:
            logger.exception(f"async_buy CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            lock.release()

    def sync_sell(self, stock_code, sell_price, sell_num, strategy_name, inner_stock_infos):
        lock = self._get_lock(stock_code)
        lock.acquire()
        try:
            sell_num *= 100
            return self.xt_trader.order_stock(
                self.account, stock_code, xtconstant.STOCK_SELL, sell_num, xtconstant.FIX_PRICE, sell_price,
                strategy_name
            )
        except Exception as e:
            logger.exception(f"async_sell CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            lock.release()

    def cancel(self, order_id):
        return self.xt_trader.cancel_order_stock(self.account, order_id)

    def get_holding(self):
        return self.xt_trader.query_stock_positions(self.account)

    def get_transactions(self):
        return self.xt_trader.query_stock_trades(self.account)

    def get_hanging(self):
        return self.xt_trader.query_stock_orders(self.account, True)

    def query_by_order_id(self, order_id):
        return self.xt_trader.query_stock_order(self.account, order_id)

    def get_asset(self):
        return self.xt_trader.query_stock_asset(self.account)

    def get_history_deal_list(self):
        return self.xt_trader.query_data(self.account, "C:\\Users\\Administrator\\Desktop\\deal.csv", "deal")

    def export_history_deal_list(self):
        return self.xt_trader.export_data(self.account, 'C:\\Users\\Administrator\\Desktop\\deal.csv', 'stkFundFlow', 1747756800)
        # return self.xt_trader.export_data(self.account,
        #                                   '/c/Users/Administrator/Desktop/deal2.csv', 'deal'
        #                                   )
    def query_stock_trades(self):
        return self.xt_trader.query_stock_trades(self.account)


class TraderStrategyService:
    def __init__(self, platform, min_bid_money, max_bid_money, frozen_money, trader_service, strategy_name):
        self.locks = {}
        self.max_bid_money = max_bid_money
        self.frozen_money = frozen_money
        self.platform = platform
        self.min_bid_money = min_bid_money
        self.trader_service = trader_service
        self.strategy_name = strategy_name
        self.processor = adaptive_task_processor.AdaptiveTaskProcessor()
        self.active_sell_orders = {}

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        lock_key = self.platform + "_" + stock_code
        if lock_key not in self.locks:
            self.locks[lock_key] = threading.Lock()
        return self.locks[lock_key]

    def to_buy(self, inner_stock_infos, target_index_infos, stock_code, limit_price, appraisal, fresh_holding = True):
        stock_info = inner_stock_infos[stock_code]
        index_info = target_index_infos[data_loader.get_group_code(stock_info['target_index'], stock_code)]
        increase_rate = index_info.get('increase_rate', 0)
        # 卖不用管，买需要加锁，防止重复购买
        logger.info(f"to buy {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        lock = self._get_lock(stock_code)
        lock.acquire()
        logger.info(f"to buy {stock_code}, got lock{get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        try:
            # 当卖盘不为空，并且卖1出价小于估值时，进一步再判断溢价空间
            if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0]:
                bid_price = 0
                bid_num = 0
                bid_money = 0
                premium = 0
                first_premium = 0
                hold_num = stock_info['hold_num']
                # 已经持仓的金额
                holding_money = round(stock_info['hold_num'] * 100 * stock_info['askPrice'][0], 2)
                logger.info(f"holding_money: {holding_money}, index_holding_money: {index_info['index_total_market_value']}")
                max_able_bid_money = self.max_bid_money - holding_money
                if max_able_bid_money < self.min_bid_money:
                    logger.info(f"{stock_code}已持有足够多了， holding_money: {holding_money}元, {max_able_bid_money} < {self.min_bid_money}")
                    return
                # 计算当前指数共持仓多少钱，
                index_unused_money_capacity = self.max_bid_money * 2 - index_info['index_total_market_value']
                logger.info(f"该指数共持仓已达: {index_info['index_total_market_value']}元，还有{index_unused_money_capacity}元额度可买")
                if index_unused_money_capacity < max_able_bid_money:
                    max_able_bid_money = index_unused_money_capacity

                if max_able_bid_money < self.min_bid_money:
                    logger.info(f"{stock_code} 对应的相同的指数已持有足够多了， holding_money: {index_info['index_total_market_value']}元, {max_able_bid_money} < {self.min_bid_money}")
                    return
                asset = self.trader_service.get_asset()
                logger.info(f"此时cash： {asset.cash}")
                if asset.cash - self.frozen_money <= max_able_bid_money:
                    max_able_bid_money = asset.cash - self.frozen_money

                for i, price in enumerate(stock_info['askPrice']):
                    if price > limit_price:
                        continue
                    if bid_money <= max_able_bid_money:
                        bid_price = round(price, 6)
                        bid_num += stock_info['askVol'][i]
                        bid_money += bid_price * bid_num * 100
                        # 如果超过了最大单笔限上额，减去一点
                        if bid_money > max_able_bid_money:
                            bid_num -= math.ceil((bid_money - max_able_bid_money) / bid_price / 100)
                            bid_money -= bid_num * 100 * bid_price
                            break

                # 已经持有的够多了，没有再买的空间了
                if max_able_bid_money < self.min_bid_money:
                    logger.info(f"{stock_code} 可买的太少了 {max_able_bid_money} < {self.min_bid_money}")
                    return
                if bid_money == 0:
                    logger.info(f"bid_money: {bid_money}, give up to buy")
                    return
                # 可买的数量太少也放弃出价
                if bid_money < self.min_bid_money:
                    logger.info(f"{stock_info['name']}, {stock_info['code']}, 可买数量太少, {bid_money} < {self.min_bid_money}")
                    return

                if bid_num > 0 and bid_price > 0:
                    # 下单
                    remark = f"买入日志{get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}: 买入{stock_code}," \
                             f"折价率: {round((appraisal - stock_info['askPrice'][0]) / stock_info['askPrice'][0] * 100, 4)}%，" \
                             f"估值{appraisal},报价{bid_price},{bid_num}手, 目前卖盘{stock_info['askPrice']},{stock_info['askVol']}, 指数{index_info}, 当前持有{hold_num}"
                    logger.info(remark)
                    # 把bid_num放入到hold_num，防止超买
                    stock_info.update({'hold_num': hold_num + round(bid_num)})
                    # 出价以后，把卖盘中卖一的队列数量进行相应的减掉
                    ask_vol_remain = round(inner_stock_infos[stock_code]['askVol'][0] - bid_num)
                    inner_stock_infos[stock_code]['askVol'][0] = max(0, ask_vol_remain)
                    self.processor.submit_task(self.order_buy_thread, stock_code, bid_price, bid_num, stock_info, hold_num, inner_stock_infos, target_index_infos, fresh_holding)
                    logger.info(f"inner_stock_info: {stock_info}")
                    logger.info(f"target_index_info: {index_info}")
                    logger.info(f"参数appraisal: {appraisal}, 实时计算appraisal: {round(float(stock_info['last_net_worth']) * (1 + float(increase_rate) * 0.95), 4)}")
                    return
                else:
                    logger.info(f"to buy gives up, bid_num: {bid_num}, bid_price: {bid_price}, bid_money: {bid_money}")
        except Exception as e:
            logger.exception(f"to_buy CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            logger.info(f"release lock {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            lock.release()

    def to_sell(self, inner_stock_infos, target_index_infos, stock_code, limit_price, appraisal, fresh_holding = True):
        stock_info = inner_stock_infos[stock_code]
        index_info = target_index_infos[data_loader.get_group_code(stock_info['target_index'], stock_code)]
        increase_rate = index_info.get('increase_rate', 0)
        logger.info(f"to_sell {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        if len(stock_info['bidPrice']) > 0 and stock_info['hold_can_use_num'] > 0:
            sell_price = stock_info['bidPrice'][0]
            sell_num = 0
            total_money = 0
            premium = 0
            for i, price in enumerate(stock_info['bidPrice']):
                if price < limit_price:
                    continue
                if price >= limit_price:
                    premium = round((price - appraisal) / appraisal * 100, 4)
                    # if premium < premium_threshold:
                    #     break
                    sell_price = round(price, 6)
                    sell_num += stock_info['bidVol'][i]
                    if sell_num > stock_info['hold_can_use_num']:
                        sell_num = stock_info['hold_can_use_num']
                    total_money += sell_num * 100 * price
                    if sell_num >= stock_info['hold_can_use_num']:
                        break
            # 可卖的太少了，不值当的
            if total_money < self.min_bid_money and sell_num < stock_info['hold_can_use_num']:
                logger.info(f"{stock_code}, 可卖的太少了{total_money}, sell_num: {sell_num}, limit_price: {limit_price}, hold_can_use_num: {stock_info['hold_can_use_num']}")
                return
            if sell_num > 0 and sell_price > 0 and stock_info['hold_can_use_num'] > 0:
                remark = f"卖出日志: {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}, 卖出{stock_code}, premium: {premium}," \
                         f"估值{appraisal},报价{sell_price},{sell_num}手, 目前买盘{stock_info['bidPrice']},{stock_info['bidVol']}, 指数{index_info}"
                logger.info(remark)
                # 出价以后，把买盘中买一的队列数量进行相应的减掉
                bid_vol_remain = round(inner_stock_infos[stock_code]['bidVol'][0] - sell_num)
                inner_stock_infos[stock_code]['bidVol'][0] = max(0, bid_vol_remain)
                self.processor.submit_task(self.order_sell_thread, stock_code, sell_price, sell_num, stock_info, inner_stock_infos, target_index_infos, fresh_holding)
                logger.info(f"inner_stock_info: {stock_info}")
                logger.info(f"target_index_info: {index_info}")
                logger.info(f"参数appraisal: {appraisal}, 实时计算appraisal: {round(float(stock_info['last_net_worth']) * (1 + float(increase_rate) * 0.9), 4)}")

    def sell_then_buy(self, inner_stock_infos, target_index_infos, first_buy_queue_node, first_sell_queue_node):
        self.processor.submit_task(self.order_sell_then_buy_thread, inner_stock_infos, target_index_infos, first_buy_queue_node, first_sell_queue_node)

    def to_cancel(self, sell_stock_code, buy_stock_code):
        hangings = self.trader_service.get_hanging()
        for item in hangings:
            if item.stock_code == sell_stock_code or item.stock_code == buy_stock_code:
                logger.info(f"撤销委托, {sell_stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]},  "
                            f"order_id: {item.order_id}, 撤销结果: {self.trader_service.cancel(item.order_id)}")

    def order_buy_thread(self, stock_code, bid_price, bid_num, stock_info, hold_num, inner_stock_infos, target_index_infos, fresh_holding):
        lock = self._get_lock(stock_code)
        lock.acquire()
        try:
            order_id = self.trader_service.async_buy(stock_code, bid_price, bid_num, self.strategy_name, stock_info, hold_num)
            if order_id:
                logger.info(f"order_buy_thread order_id: {order_id}")
                time.sleep(2)
                order = self.trader_service.query_by_order_id(int(order_id))
                if not order:
                    # 撤单
                    self.trader_service.cancel(order_id)
                else:
                    if order.order_status != xtconstant.ORDER_SUCCEEDED:
                        # 撤单
                        self.trader_service.cancel(order_id)
                # 更新持有信息
                if fresh_holding:
                    time.sleep(2)
                    data_loader.fresh_holding(inner_stock_infos, target_index_infos, self.trader_service.get_holding())
            else:
                logger.error("下单失败")
            logger.info(f"buy executed over {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        except Exception as e:
            logger.exception(f"order_buy_thread CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            lock.release()
            logger.info(f"order_buy_thread release lock {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")

    def order_sell_thread(self, stock_code, sell_price, sell_num, stock_info, inner_stock_infos, target_index_infos, fresh_holding=True):
        order_id = self.trader_service.sync_sell(stock_code, sell_price, sell_num, self.strategy_name, inner_stock_infos)
        logger.info(f"order_sell_thread 卖出orderid: {order_id}")
        if order_id:
            order = self.trader_service.query_by_order_id(int(order_id))
            logger.info(f"卖出结果: {order} {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            # 0.5秒撤单，如果卖成功了就撤单失败无所谓
            time.sleep(2)
            self.trader_service.cancel(order_id)
        # 更新持有信息
        if fresh_holding:
            time.sleep(1.5)
            data_loader.fresh_holding(inner_stock_infos, target_index_infos, self.trader_service.get_holding())
        logger.info(f"sell executed over {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")

    def order_sell_then_buy_thread(self, inner_stock_infos, target_index_infos, first_buy_queue_node, first_sell_queue_node):
        lock = self._get_lock(first_sell_queue_node.code)
        try:
            logger.info(f"order_sell_then_buy_thread start {first_buy_queue_node.code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            self.to_sell(inner_stock_infos, target_index_infos, first_buy_queue_node.code, float(first_buy_queue_node.price), float(first_buy_queue_node.appraisal), False)
            time.sleep(1.5)

            logger.info(f"prepare to buy {first_sell_queue_node.code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            lock.acquire()
            self.to_buy(inner_stock_infos, target_index_infos, first_sell_queue_node.code, float(first_sell_queue_node.price), float(first_sell_queue_node.appraisal), False)
            lock.release()

            time.sleep(1.5)
            self.to_cancel(first_sell_queue_node.code, first_buy_queue_node.code)
            time.sleep(1.5)
            data_loader.fresh_holding(inner_stock_infos, target_index_infos, self.trader_service.get_holding())
            logger.info(f"order_sell_then_buy_thread end {first_sell_queue_node.code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        except Exception as e:
            logger.exception(f"order_sell_then_buy_thread CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            lock.release()

    def get_active_sell_order_details(self, stock_code):
        order_id = self.active_sell_orders.get(stock_code)
        if not order_id:
            return None
        try:
            order = self.trader_service.query_by_order_id(int(order_id))
            if order:
                # 只有明确已终止的状态才清除跟踪记录
                if order.order_status in [
                    xtconstant.ORDER_CANCELED,
                    xtconstant.ORDER_PART_CANCEL,
                    xtconstant.ORDER_PARTSUCC_CANCEL,
                    xtconstant.ORDER_SUCCEEDED,
                    xtconstant.ORDER_JUNK
                ]:
                    logger.info(f"主动卖单已结束 {order_id} 状态: {order.order_status}，移除跟踪")
                    self.active_sell_orders.pop(stock_code, None)
                    return None
                # 订单仍然活跃（未报/待报/已报/部分成交/待撤）
                return order
            else:
                # API返回None，可能是临时查询失败，保留跟踪记录不删除
                logger.warning(f"query_by_order_id 返回 None, order_id: {order_id}，暂不清除跟踪记录")
                return None
        except Exception as e:
            # 查询异常，保留跟踪记录不删除，等待下次重试
            logger.error(f"查询挂单详情异常 {order_id}: {e}")
            return None

    def cancel_and_wait(self, code, order_id):
        logger.info(f"开始撤销挂单 {order_id} 代码: {code}")
        self.trader_service.cancel(order_id)
        t_start = time.perf_counter()
        while time.perf_counter() - t_start < 1.5:
            order = self.trader_service.query_by_order_id(int(order_id))
            if order:
                if order.order_status in [
                    xtconstant.ORDER_CANCELED,
                    xtconstant.ORDER_PART_CANCEL,
                    xtconstant.ORDER_PARTSUCC_CANCEL,
                    xtconstant.ORDER_SUCCEEDED,
                    xtconstant.ORDER_JUNK
                ]:
                    logger.info(f"挂单 {order_id} 撤单/结束完成，最终状态: {order.order_status}")
                    return order
            time.sleep(0.01)
        logger.warning(f"等待挂单 {order_id} 撤单超时")
        return None
    def cancel_active_sell_task(self, code, order_id, stock_info):
        """仅撤销主动卖单，不再重新挂单（防割韭菜/溢价收窄场景）"""
        lock = self._get_lock(code)
        if not lock.acquire(blocking=False):
            logger.info(f"Lock for {code} is already held. Skipping cancel_active_sell_task.")
            return
        try:
            order = self.cancel_and_wait(code, order_id)
            cancelled_lots = 0
            if order:
                cancelled_shares = order.order_volume - order.traded_volume
                cancelled_lots = cancelled_shares // 100
            else:
                order = self.trader_service.query_by_order_id(int(order_id))
                if order:
                    cancelled_shares = order.order_volume - order.traded_volume
                    cancelled_lots = cancelled_shares // 100

            if cancelled_lots > 0:
                stock_info['hold_can_use_num'] += cancelled_lots
                logger.info(f"防割韭菜撤单完成，恢复可用持仓: 可用={stock_info['hold_can_use_num']}, 总数={stock_info['hold_num']}")

            self.active_sell_orders.pop(code, None)
        except Exception as e:
            logger.exception(f"cancel_active_sell_task 执行异常 {code}: {e}")
        finally:
            lock.release()

    def cancel_and_sell_task(self, code, active_order_id, limit_price, appraisal, stock_info, inner_stock_infos, target_index_infos):
        lock = self._get_lock(code)
        if not lock.acquire(blocking=False):
            logger.info(f"Lock for {code} is already held. Skipping cancel_and_sell_task.")
            return
        try:
            order = self.cancel_and_wait(code, active_order_id)
            cancelled_lots = 0
            if order:
                cancelled_shares = order.order_volume - order.traded_volume
                cancelled_lots = cancelled_shares // 100
            else:
                order = self.trader_service.query_by_order_id(int(active_order_id))
                if order:
                    cancelled_shares = order.order_volume - order.traded_volume
                    cancelled_lots = cancelled_shares // 100

            if cancelled_lots > 0:
                stock_info['hold_can_use_num'] += cancelled_lots
                logger.info(f"撤单后更新本地持仓: 可用={stock_info['hold_can_use_num']}, 总数={stock_info['hold_num']}")

            self.active_sell_orders.pop(code, None)

            if stock_info['hold_can_use_num'] > 0:
                sell_num = stock_info['hold_can_use_num']
                self.to_sell(
                    inner_stock_infos, target_index_infos, code, limit_price, appraisal, True
                )
                stock_info['hold_can_use_num'] -= sell_num
        except Exception as e:
            logger.exception(f"cancel_and_sell_task 执行异常 {code}: {e}")
        finally:
            lock.release()

    def cancel_and_place_active_sell_task(self, code, active_order_id, desired_price, total_available_lots, stock_info, inner_stock_infos):
        lock = self._get_lock(code)
        if not lock.acquire(blocking=False):
            logger.info(f"Lock for {code} is already held. Skipping cancel_and_place_active_sell_task.")
            return
        try:
            order = self.cancel_and_wait(code, active_order_id)
            cancelled_lots = 0
            if order:
                cancelled_shares = order.order_volume - order.traded_volume
                cancelled_lots = cancelled_shares // 100
            else:
                order = self.trader_service.query_by_order_id(int(active_order_id))
                if order:
                    cancelled_shares = order.order_volume - order.traded_volume
                    cancelled_lots = cancelled_shares // 100

            if cancelled_lots > 0:
                stock_info['hold_can_use_num'] += cancelled_lots
                logger.info(f"撤单更新挂单持仓: 可用={stock_info['hold_can_use_num']}, 总数={stock_info['hold_num']}")

            self.active_sell_orders.pop(code, None)

            sell_num = total_available_lots
            if sell_num > 0:
                # 等待柜台异步释放可用持仓余额，避免触发 [251005][证券可用数量不足]
                time.sleep(0.2)
                order_id = self.trader_service.sync_sell(code, desired_price, sell_num, self.strategy_name, inner_stock_infos)
                if order_id and order_id > 0:
                    self.active_sell_orders[code] = order_id
                    stock_info['hold_can_use_num'] = max(0, stock_info['hold_can_use_num'] - sell_num)
                    logger.info(f"挂出新主动卖单 {order_id} 价格: {desired_price}, 数量: {sell_num}手. 可用={stock_info['hold_can_use_num']}")
                else:
                    logger.error(f"主动卖单挂单失败 {code} 价格: {desired_price}")
        except Exception as e:
            logger.exception(f"cancel_and_place_active_sell_task 执行异常 {code}: {e}")
        finally:
            lock.release()

    def place_active_sell_task(self, code, desired_price, sell_num, stock_info, inner_stock_infos):
        lock = self._get_lock(code)
        if not lock.acquire(blocking=False):
            logger.info(f"Lock for {code} is already held. Skipping place_active_sell_task.")
            return
        try:
            if stock_info['hold_can_use_num'] >= sell_num > 0:
                order_id = self.trader_service.sync_sell(code, desired_price, sell_num, self.strategy_name, inner_stock_infos)
                if order_id and order_id > 0:
                    self.active_sell_orders[code] = order_id
                    stock_info['hold_can_use_num'] -= sell_num
                    logger.info(f"成功挂出主动卖单 {order_id} 价格: {desired_price}, 数量: {sell_num}手. 可用={stock_info['hold_can_use_num']}")
                else:
                    logger.error(f"主动卖单挂单失败 {code} 价格: {desired_price}")
        except Exception as e:
            logger.exception(f"place_active_sell_task 执行异常 {code}: {e}")
        finally:
            lock.release()
