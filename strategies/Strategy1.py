# coding=utf-8
import json
import math
from decimal import Decimal
from xtquant import xtdata, xtconstant
from db import strategy_record
import helper.data_loader as data_loader
from helper import utils, spider
from datetime import datetime
import logging
from service import account
import time
import threading

logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    filename='logs/app.log',
                    filemode='a')
logger = logging.getLogger(__name__)


class Strategy1:
    def __init__(self, db, traderService):
        # 等待被初始化的全局场内基金
        self.inner_stock_infos = {}
        # 等待被初始化的全局指数
        self.target_index_infos = {}
        # 上一个交易日
        self.yesterday = data_loader.get_previous_date()
        # 单笔最大买入金额
        self.max_bid_money = 5200
        self.min_bid_money = 1000
        self.base_premium_threshold = 0.22
        self.db = db
        self.traderService = traderService
        # 多线程请求时，最多5个线程
        self.semaphore = threading.Semaphore(1)

    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos, self.traderService.get_holding())
        data_loader.load_target_index(self.inner_stock_infos, self.target_index_infos)

        SId1 = xtdata.subscribe_whole_quote(data_loader.get_all_inner_stocks_code(self.db), callback=self.stock_handler)
        SId2 = xtdata.subscribe_whole_quote(data_loader.get_all_target_index_code(self.inner_stock_infos),
                                            callback=self.index_handler)

        logger.info(f"策略1启动，订阅成功: SId1-{SId1}, SId2-{SId2}\r")

        time.sleep(5)
        # 5秒后，开始用另一种方式监听没有订阅到的指数
        logger.info(f"loading rest index...")
        rest_index_codes = data_loader.get_rest_index(self.target_index_infos)
        # 异步多线程通过第三方订阅没有检测到的指数信息
        threading.Thread(target=self.subscribe_rest_index_stock, args=(rest_index_codes,)).start()

    def stock_handler(self, msgs):
        for code in msgs:
            # logger.info(f"订阅消息: stock-  {msgs[code]}")
            self.inner_stock_infos[code].update({
                'askPrice': msgs[code]['askPrice'],
                'askVol': msgs[code]['askVol'],
                'bidPrice': msgs[code]['bidPrice'],
                'bidVol': msgs[code]['bidVol'],
                'status': True,
            })
            # logger.info(f"stock_handler-{inner_stock_infos[code]}")
            # 分析关联的code
            self.analysis_and_decision_mking(code)

    def index_handler(self, msgs):
        for code in msgs:
            # logger.info(f"订阅消息: index-{code},  {msgs[code]}")
            if msgs[code]['lastClose'] == 0:
                continue
            self.target_index_infos[utils.purified_code(code)].update({
                'start': msgs[code]['lastClose'],
                'current': msgs[code]['lastPrice'],
                'increase_rate': Decimal(
                    round((msgs[code]['lastPrice'] - msgs[code]['lastClose']) / msgs[code]['lastClose'], 6)),
                'status': True,
            })
            # logger.info(f"index_handler-{msgs[code]}")
            # 逐个分析关联的code
            for stock_code in self.target_index_infos[utils.purified_code(code)]['relation']:
                self.analysis_and_decision_mking(stock_code)

    def subscribe_rest_index_stock(self, rest_index_codes):
        while True:
            if utils.is_market_closing():
                time.sleep(3)
                continue

            # 在这里多线程执行subscribe_detail_index_stock
            for index_code in rest_index_codes:
                self.semaphore.acquire()
                threading.Thread(target=self.subscribe_detail_index_stock, args=(index_code,)).start()


            time.sleep(1)

    def subscribe_detail_index_stock(self, index_code):
        resp = spider.get_current_index_info(index_code)
        if resp is None:
            return
        current_index = Decimal(resp['current_index'])
        if current_index <= 0:
            return
        self.target_index_infos[index_code].update({
            # 只有从这里更新的指数数据有这个key，防止连接中断后依据死数据做决策
            'index_updated_time': time.time(),
            'start': resp['last_close'],
            'current': resp['current_index'],
            'increase_rate': Decimal(
                round((Decimal(resp['current_index']) - Decimal(resp['last_close'])) / Decimal(resp['last_close']), 6)),
            'status': True,
        })
        for stock_code in self.target_index_infos[utils.purified_code(index_code)]['relation']:
            self.analysis_and_decision_mking(stock_code)
        self.semaphore.release()

    def analysis_and_decision_mking(self, stock_code):
        stock_info = self.inner_stock_infos[stock_code]
        index_info = self.target_index_infos[stock_info['target_index']]
        # 来自链接第三方订阅的指数，如果更新时间超过5秒就不处理了
        if 'index_updated_time' in index_info:
            if time.time() - index_info['index_updated_time'] >= 8:
                return
        # 双方未就绪，不处理
        if index_info['status'] == False or stock_info['status'] == False:
            return

        if stock_info['last_net_worth_date'] != self.yesterday:
            # pass
            # 白天可以用，晚上就不行了
            return

        appraisal = Decimal(round(stock_info['last_net_worth'] * (Decimal(1) + index_info['increase_rate']) * (Decimal(1) - stock_info['withdraw_commission_7rate']), 6))
        # 当卖盘不为空，并且卖1出价小于估值时，进一步再判断溢价空间
        if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0] < appraisal:
            bid_price = 0
            bid_num = 0
            bid_money = 0
            premium_threshold = data_loader.get_premium(index_info['increase_rate'], self.base_premium_threshold)
            premium = 0
            first_premium = 0
            asset = self.traderService.get_asset()
            # 账户低于1000块就不操作了，意义不大
            if asset.cash < self.min_bid_money:
                return
            if asset.cash <= self.max_bid_money:
                self.max_bid_money = asset.cash
            for i, price in enumerate(stock_info['askPrice']):
                # 计算一下当前卖一的折价率
                if i == 0:
                    first_premium = round((appraisal - Decimal(price)) / Decimal(appraisal) * 100, 4)
                premium = round((appraisal - Decimal(price)) / Decimal(appraisal) * 100, 4)
                self.inner_stock_infos[stock_code].update({'premium': first_premium})
                if premium >= premium_threshold and bid_money <= self.max_bid_money:
                    bid_price = round(price, 6)
                    bid_num += stock_info['askVol'][i]
                    bid_money += bid_price * bid_num * 100
                    # 如果超过了最大单笔限上额，减去一点
                    if bid_money > self.max_bid_money:
                        bid_num -= math.ceil((bid_money - self.max_bid_money) / bid_price / 100)

            if utils.should_print(60):
                logger.info(
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}-{stock_info['name']}-{stock_info['code']},估值: {appraisal}, 卖一报价: {round(stock_info['askPrice'][0], 4)}, 折价率: {round((appraisal - Decimal(stock_info['askPrice'][0])) / Decimal(stock_info['askPrice'][0]) * Decimal(100), 4)}%")
                data_loader.print_top_variance(self.inner_stock_infos)

            # 可买的数量太少也放弃出价
            if bid_money < self.min_bid_money:
                return

            if bid_num > 0 and bid_price > 0 and stock_info['hold_status'] == 1 and stock_info['hold_num']:
                # 下单
                remark = f"买入日志: 买入{stock_code}, {datetime.now().strftime('%Y-%m-%d %H:%M:%S')},折价率: {premium}%，" \
                         f"估值{appraisal},报价{bid_price},{bid_num}手, 目前卖盘{stock_info['askPrice']},{stock_info['askVol']}, 指数{index_info}"
                logger.info(remark)
                order_id = self.traderService.async_buy(stock_code, bid_price, bid_num, "折价策略", self.inner_stock_infos)
                if order_id:
                    logger.info(f"order_id: {order_id}")
                    time.sleep(1)
                    order = self.traderService.query_by_order_id(int(order_id))
                    if order.order_status != xtconstant.ORDER_SUCCEEDED:
                        # 撤单
                        self.traderService.cancel(order_id)
                        strategy_record.add(self.db, order_id, "折价策略", stock_code, order.traded_price,
                                            order.traded_volume, index_info['current'], remark, 400)
                    if order.traded_volume > 0:
                        strategy_record.add(self.db, order_id, "折价策略", stock_code, order.traded_price,
                                            order.traded_volume, index_info['current'], remark, 300)
                else:
                    logger.error("下单失败")
                return
        if len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] >= appraisal and stock_info['hold_num'] > 0:
            sell_price = stock_info['bidPrice'][0]
            sell_num = 0
            total_money = 0
            for i, price in enumerate(stock_info['bidPrice']):
                if price >= appraisal:
                    sell_price = round(price, 6)
                    sell_num += stock_info['bidVol'][i]
                    if sell_num > stock_info['hold_num']:
                        sell_num = stock_info['hold_num']
                    total_money += sell_num * 100 * price
                    if sell_num >= stock_info['hold_num']:
                        break
            # 可卖的太少了，不值当的
            if total_money < self.min_bid_money and sell_num < stock_info['hold_num']:
                return
            if sell_num > 0 and sell_price > 0 and stock_info['hold_num'] > 0:
                remark = f"卖出日志: 卖出{stock_code}, {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}," \
                         f"估值{appraisal},报价{sell_price},{sell_num}手, 目前买盘{stock_info['bidPrice']},{stock_info['bidVol']}, 指数{index_info}"
                logger.info(remark)
                order_id = self.traderService.sync_sell(stock_code, sell_price, sell_num, "折价策略",
                                                        self.inner_stock_infos)
                strategy_record.add(self.db, order_id, "折价策略", stock_code, sell_price,
                                    sell_num*100, index_info['current'], remark, 200)