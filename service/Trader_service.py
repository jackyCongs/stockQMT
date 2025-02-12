# coding=utf-8

from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant import xtconstant
from service import account
import time
import threading
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)


class TradeCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        """
        连接断开
        :return:
        """
        logger.info("connection lost")

    def on_stock_order(self, order):
        """
        委托回报推送
        :param order: XtOrder对象
        :return:
        """
        logger.info("on order callback:")
        logger.info(order.stock_code, order.order_status, order.order_sysid)

    def on_stock_trade(self, trade):
        """
        成交变动推送
        :param trade: XtTrade对象
        :return:
        """
        logger.info("on trade callback")
        logger.info(trade.account_id, trade.stock_code, trade.order_id)

    def on_order_error(self, order_error):
        """
        委托失败推送
        :param order_error:XtOrderError 对象
        :return:
        """
        logger.info("on order_error callback")
        logger.info(order_error.order_id, order_error.error_id, order_error.error_msg)

    def on_cancel_error(self, cancel_error):
        """
        撤单失败推送
        :param cancel_error: XtCancelError 对象
        :return:
        """
        logger.info("on cancel_error callback")
        logger.info(cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg)

    def on_order_stock_async_response(self, response):
        """
        异步下单回报推送
        :param response: XtOrderResponse 对象
        :return:
        """
        logger.info("on_order_stock_async_response")
        logger.info(response.account_id, response.order_id, response.seq)

    def on_account_status(self, status):
        """
        :param response: XtAccountStatus 对象
        :return:
        """
        logger.info("on_account_status")
        logger.info(status.account_id, status.account_type, status.status)


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
        self.xt_trader.register_callback(TradeCallback)
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
        return self.xt_trader.query_stock_orders(account, True)

    def query_by_order_id(self, order_id):
        return self.xt_trader.query_stock_order(self.account, order_id)

    def get_asset(self):
        return self.xt_trader.query_stock_asset(self.account)