# coding=utf-8
import math
from datetime import datetime
from db import strategy_record
from xtquant import xtdata, xtconstant
import logging
import helper.data_loader as data_loader
import threading

logging.basicConfig(level=logging.INFO, format='%(message)s', filename='logs/app.log', filemode='a')
logger = logging.getLogger(__name__)

## T+0 高频交易策略
class Strategy2:
    def __init__(self, db, traderService):
        self.stock_T_0_codes = ["159915"]
        self.stock_T_0_infos = {}
        # 最大、小买入金额
        self.bid_max_money = 2200
        self.bid_min_money = 2000
        self.holding_money = 0
        self.trade_cond_vol = 100
        self.db = db
        self.locks = {}
        self.traderService = traderService
        data_loader.load_stock(self.db, self.stock_T_0_codes, self.stock_T_0_infos, self.traderService.get_holding())

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    # 启动策略
    def run(self):
        subscribe_id = xtdata.subscribe_whole_quote(
            data_loader.convert_enhance_code(self.stock_T_0_codes),
            callback=self.analyse)
        print(f"订阅结果: {subscribe_id}")

    # 拿到订阅数据，实时分析
    def analyse(self, msgs):
        for code in msgs:
            #print(f"信息输出 code: {code}, {msgs[code]}")
            self.stock_T_0_infos[code].update({
                'askPrice': msgs[code]['askPrice'],
                'askVol': msgs[code]['askVol'],
                'bidPrice': msgs[code]['bidPrice'],
                'bidVol': msgs[code]['bidVol'],
                'status': True,
            })
            is_holding = False
            # 变化趋势数据记录下来给AI做训练，到第三版本使用
            is_holding = self.stock_T_0_infos[code]['hold_num'] > 0
            # 不符合趋势 or 剩余数量过多，直接忽略
            #print(self.stock_T_0_infos)
            if not self.update_trend_return(msgs[code], self.stock_T_0_infos[code], is_holding):
                #print(self.stock_T_0_infos)
                continue
            print('go on...')
            if is_holding:
                self.sell(code, msgs[code]['bidPrice'][0], self.stock_T_0_infos[code]['hold_num'])
            elif self.holding_money > 0:
                self.buy(code, msgs[code]['askPrice'][0], (self.holding_money-self.bid_min_money) / msgs[code]['askPrice'][0])
            else:
                self.buy(code, msgs[code]['askPrice'][0], self.bid_min_money / msgs[code]['askPrice'][0])


    def update_trend_return(self, msg, info, is_holding):
        def update_trend(trend_price_key, trend_vol_key, price_key, vol_key):
            current_price = msg.get(price_key, [0])[0]  # 默认0防索引错误
            current_vol = msg.get(vol_key, [0])[0]
            trend_price = info.get(trend_price_key, 0)
            if trend_price == 0 or trend_price != current_price:
                info[trend_price_key] = current_price
                info[trend_vol_key] = [current_vol, 0, 0]
            else:
                # 保持最近三次vol记录，新数据插入首位
                prev_vol = info.get(trend_vol_key, [0, 0, 0])
                info[trend_vol_key] = [current_vol, prev_vol[0], prev_vol[1]]

        update_trend('askTrendPrice', 'askTrendVol', 'askPrice', 'askVol')
        update_trend('bidTrendPrice', 'bidTrendVol', 'bidPrice', 'bidVol')
        if is_holding:
            trend_vol = info.get('bidTrendVol', [])
            current_vol = msg.get('bidVol', [0])[0]
        else:
            trend_vol = info.get('askTrendVol', [])
            current_vol = msg.get('askVol', [0])[0]
        is_decreasing = (trend_vol[0] < trend_vol[1] < trend_vol[2]) or (trend_vol[0] < trend_vol[2] and math.pow(2 * (trend_vol[1] - trend_vol[2]), 2) < math.pow(trend_vol[0] - trend_vol[1], 2))
        print(f"{info.get('name')}, is_decreasing:{is_decreasing} ,bidTrendPrice: {info.get('bidTrendPrice', 0)}, {info.get('bidTrendVol', [])}, askTrendPrice: {info.get('askTrendPrice', 0)}, {info.get('askTrendVol', [])}")
        return is_decreasing and (current_vol < self.trade_cond_vol)


    def buy(self, stock_code, price, num):
        lock = self._get_lock(stock_code)
        lock.acquire()

        bid_price = round(price, 6)
        bid_num = math.ceil(num / 100)
        if ((bid_price * bid_num * 100 < self.bid_min_money
                or bid_price * bid_num * 100 > self.bid_max_money)
                or bid_price * bid_num * 100 + self.holding_money > self.bid_max_money):
            lock.release()
            return
        # order_id = self.traderService.async_buy(stock_code, bid_price, bid_num, "T0高频交易策略", self.stock_T_0_infos)
        self.record(stock_code, '买入', bid_price, bid_num)
        hold_num = self.stock_T_0_infos[stock_code]['hold_num']
        self.stock_T_0_infos[stock_code].update({'hold_num': hold_num + num})
        self.holding_money += bid_price * bid_num * 100

        lock.release()


    def sell(self, stock_code, price, num):
        lock = self._get_lock(stock_code)
        lock.acquire()

        if num > self.stock_T_0_infos[stock_code]['hold_num']:
            lock.release()
            return
        bid_price = round(price, 6)
        self.record(stock_code, '卖出', bid_price, num)
        hold_num = self.stock_T_0_infos[stock_code]['hold_num']
        self.stock_T_0_infos[stock_code].update({'hold_num': hold_num - num})
        self.holding_money -= bid_price * num * 100

    # 记录，后面数据统计复盘
    def record(self, stock_code, type, price, num):
        remark = (f"************************{type}日志: {type}{stock_code}, {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                  f",报价{price},{num}手")
        print(remark)
        strategy_record.add(self.db, "", "T0高频交易策略", stock_code, price,
                            num * 100, 0, remark, 200)