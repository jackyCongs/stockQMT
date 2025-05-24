# coding=utf-8

from xtquant.xttrader import XtQuantTrader
from xtquant import xtconstant
from service import account, TradeCallback
import time
import threading
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)


class Trader_service:
    def __init__(self, session_id):
        self.path = account.get_path()
        self.session_id = session_id
        self.account = account.get_account()
        self.xt_trader = self._create_trader()
        self._connect()
        self.locks = {}
        self.asset = self.xt_trader.query_stock_asset(self.account)

    def _create_trader(self):
        # 创建交易对象
        return XtQuantTrader(self.path, self.session_id)

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    def _connect(self):
        # 建立交易连接
        callback = TradeCallback.TradeCallback()
        self.xt_trader.register_callback(callback)
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
            if previous_hold_num != inner_stock_infos[stock_code]['hold_num']:
                return
            inner_stock_infos[stock_code].update({'hold_num': previous_hold_num + bid_num})
            bid_num *= 100
            return self.xt_trader.order_stock(
                self.account, stock_code, xtconstant.STOCK_BUY, bid_num, xtconstant.FIX_PRICE, bid_price,
                strategy_name
            )
        finally:
            lock.release()

    def sync_sell(self, stock_code, sell_price, sell_num, strategy_name, inner_stock_infos):
        lock = self._get_lock(stock_code)
        lock.acquire()
        try:
            if inner_stock_infos[stock_code]['hold_num'] == 0:
                return
            # 更新持有数量
            remain_num = inner_stock_infos[stock_code]['hold_num']
            inner_stock_infos[stock_code].update({'hold_num': remain_num - sell_num})

            sell_num *= 100
            return self.xt_trader.order_stock(
                self.account, stock_code, xtconstant.STOCK_SELL, sell_num, xtconstant.FIX_PRICE, sell_price,
                strategy_name
            )
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
