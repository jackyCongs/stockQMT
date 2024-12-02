# coding=utf-8

from xtquant.xttrader import XtQuantTrader
from xtquant import xtconstant
from service import account, TradeCallback
import time
import threading


class TraderService:
    def __init__(self):
        self.path = account.get_path()
        self.session_id = 1
        self.account = account.get_account()
        self.xt_trader = self._create_trader()
        self._connect()
        self.locks = {}

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
        while True:
            self.xt_trader.register_callback(TradeCallback.TradeCallback)
            self.xt_trader.start()
            connect_result = self.xt_trader.connect()
            if connect_result == 0:
                print("trade connected success")
                break
            else:
                print("trade connected FAILED!!! retrying.......")
                time.sleep(3)

        # 订阅账户
        while True:
            subscribe_result = self.xt_trader.subscribe(self.account)
            if subscribe_result == 0:
                print("account trading subscribed success")
                break
            else:
                print("account trading subscribed FAILED!!! retrying.......")
                time.sleep(3)


    # 异步下单
    def async_buy(self, stock_code, bid_price, bid_num, strategy_name, remark, inner_stock_infos):
        # 抢锁
        lock = self._get_lock(stock_code)
        lock.acquire()

        try:
            # 调用之前可能会有并发问题，在锁中需要再校验一词
            if inner_stock_infos[stock_code]['hold_status'] != 0:
                return
            inner_stock_infos[stock_code].update({'hold_status': 2})
            bid_num *= 100
            return self.xt_trader.order_stock_async(
                self.account, stock_code, xtconstant.STOCK_BUY, bid_num, xtconstant.FIX_PRICE, bid_price,
                strategy_name, remark
            )
        finally:
            lock.release()

    def sync_sell(self):
        pass

    def cancel(self):
        pass
