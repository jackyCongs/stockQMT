# coding=utf-8
import json
import math
from decimal import Decimal
from logging import exception

from xtquant import xtdata, xtconstant
from db import strategy_record
import helper.data_loader as data_loader
from helper import utils, spider, date_utils
from datetime import datetime
import logging
import time
import threading
from service import stock_queue

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
        self.min_bid_money = 500
        self.base_premium_threshold = 0.25
        self.db = db
        self.traderService = traderService
        # 多线程请求时，最多5个线程
        self.semaphore = threading.Semaphore(1)
        self.sell_queue = stock_queue.StockQueue()
        self.buy_queue = stock_queue.StockQueue()
        self.locks = {}

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos)
        data_loader.load_target_index(self.inner_stock_infos, self.target_index_infos)
        data_loader.fresh_holding(self.inner_stock_infos, self.traderService.get_holding())

        SId1 = xtdata.subscribe_whole_quote(data_loader.get_all_inner_stocks_code(self.db), callback=self.stock_handler)
        SId2 = xtdata.subscribe_whole_quote(data_loader.get_all_target_index_code(self.inner_stock_infos),
                                            callback=self.index_handler)

        logger.info(f"策略1启动，订阅成功: SId1-{SId1}, SId2-{SId2}\r")

        time.sleep(5)
        # 5秒后，开始用另一种方式监听没有订阅到的指数
        logger.info(f"loading rest index...")
        rest_index_codes = data_loader.get_rest_index(self.target_index_infos)
        logger.info(f"rest_index_codes数量: {len(rest_index_codes)}, {rest_index_codes}")
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
            time.sleep(3)

    def subscribe_detail_index_stock(self, index_code):
        try:
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
        except IOError as e:
            print(e)
            return
        finally:
            self.semaphore.release()

    def analysis_and_decision_mking(self, stock_code):
        if utils.is_market_closing():
            return
        stock_info = self.inner_stock_infos[stock_code]
        index_info = self.target_index_infos[stock_info['target_index']]
        # 来自链接第三方订阅的指数，如果更新时间超过5秒就不处理了
        if 'index_updated_time' in index_info:
            if time.time() - index_info['index_updated_time'] >= 4:
                self.sell_queue.remove_stock(stock_code)
                self.buy_queue.remove_stock(stock_code)
                return
        # 双方未就绪，不处理
        if index_info['status'] == False or stock_info['status'] == False:
            return

        if stock_info['last_net_worth_date'] != self.yesterday:
            # 白天可以用，晚上就不行了
            return
        # 维护两个队列
        self.maintain_premium_queues(stock_code, stock_info, index_info)
        # print()
        # print("#########################")
        # print("sell")
        # self.sell_queue.print_queue()
        # print("buy")
        # self.buy_queue.print_queue()
        # print("#########################")
        # return

        # 9点35以后开始执行
        if not utils.is_market_after_35():
            return
        # 买一队列中，只有premium大于0就会立即被卖，所以只需要取队列头的第一个数据
        first_buy_queue_node = self.buy_queue.head
        first_sell_queue_node = self.sell_queue.head
        if first_buy_queue_node is not None and first_buy_queue_node.code == stock_code and first_buy_queue_node.premium > 0:
            self.buy_queue.remove_stock(stock_code)
            self.to_sell(stock_code, first_buy_queue_node.price, first_buy_queue_node.appraisal)
            # 如果当前stock_code能卖，就一定不会有买入机会，直接结束
            return
        # 如果钱够，遇到好的委卖数据果断买入
        asset = self.traderService.get_asset()
        if asset.cash >= self.min_bid_money:
            if first_sell_queue_node is not None and first_sell_queue_node.code == stock_code:
                self.sell_queue.remove_stock(stock_code)
                self.to_buy(stock_code, first_sell_queue_node.price, first_sell_queue_node.appraisal)
            return
        else:
            ## 如果钱不够了，买卖队列进行匹配，如果先卖后买有利 then do it
            # 如果买卖队列没有，无法匹配直接结束
            if first_sell_queue_node is None or first_buy_queue_node is None:
                return
            # 必须更新到自己的时候才能进行决策，否则old data可能已经失效了
            if first_sell_queue_node.code != stock_code and first_buy_queue_node.code != stock_code:
                return
            # 需要保证买卖队列的数据都是最新的
            if (date_utils.get_current_millisecond() - first_sell_queue_node.update_time) / 1000 > 2.8:
                self.sell_queue.remove_stock(first_sell_queue_node.code)
                return
            if (date_utils.get_current_millisecond() - first_buy_queue_node.update_time) / 1000 > 2.8:
                self.sell_queue.remove_stock(first_buy_queue_node.code)
                return
            # 买卖队列有利，并且可买的两完全覆盖卖的量,sell是正数，buy是负数，
            # 防止卖了买不进去，卖的premium差必须小于基准值
            # print(f"[先卖后买]日志: 第一个条件[{(first_sell_queue_node.premium > Decimal(abs(first_buy_queue_node.premium)) + Decimal(self.base_premium_threshold))}]"
            #       f"第二个条件[{(first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_num'] * first_buy_queue_node.price)}]"
            #       f"第三个条件[{self.inner_stock_infos[first_buy_queue_node.code]['hold_num'] > 0}, {self.inner_stock_infos[first_buy_queue_node.code]['hold_num']}]"
            #       f"第四个条件[{first_buy_queue_node.premium > -self.base_premium_threshold}]"
            #       f"一 {first_sell_queue_node.premium} > {abs(first_buy_queue_node.premium)} + {Decimal(self.base_premium_threshold)}"
            #       f"二 {first_sell_queue_node.quantity} * {first_sell_queue_node.price} >= {self.inner_stock_infos[first_buy_queue_node.code]['hold_num']} * {first_buy_queue_node.price}"
            #       f"四 {first_buy_queue_node.premium} > {-self.base_premium_threshold}")
            if ((first_sell_queue_node.premium > Decimal(abs(first_buy_queue_node.premium)) + Decimal(self.base_premium_threshold)) and
                    (first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_num'] * first_buy_queue_node.price) and
                    self.inner_stock_infos[first_buy_queue_node.code]['hold_num'] > 0 and first_buy_queue_node.premium > -self.base_premium_threshold):
                # 操作之前，先从队列中移出去
                self.buy_queue.remove_stock(first_buy_queue_node.code)
                self.sell_queue.remove_stock(first_sell_queue_node.code)
                # 先卖、后买、最后如果没有买成功取消委托(买和卖的都取消)
                print(f"队列策略[先卖后买]触发: "
                      f" {first_buy_queue_node.code}卖出, price:{round(first_buy_queue_node.price, 4)} quantity:{first_buy_queue_node.quantity} appraisal:{round(first_buy_queue_node.appraisal, 5)} premium差:{round(first_buy_queue_node.premium, 4)};"
                      f"{first_sell_queue_node.code}买入,price:{round(first_sell_queue_node.price, 4)} quantity:{first_sell_queue_node.quantity} appraisal:{round(first_sell_queue_node.appraisal, 5)} premium差:{round(first_sell_queue_node.premium, 4)};")
                self.to_sell(first_sell_queue_node.code, first_sell_queue_node.price, first_sell_queue_node.appraisal)
                self.to_buy(first_buy_queue_node.code, first_buy_queue_node.price, first_buy_queue_node.appraisal)
                time.sleep(0.5)
                # self.to_cancel(first_sell_queue_node.code, first_buy_queue_node.code)
                return

    def maintain_premium_queues(self, stock_code, stock_info, index_info):
        # 计算的委卖的
        # premium符合要求，并且委卖大于最小买入金额，并且已经持有的金额不超过最大限制，维护到双向链表队列中
        appraisal = Decimal(round(stock_info['last_net_worth'] * (Decimal(1) + index_info['increase_rate']) * (
                    Decimal(1) - stock_info['withdraw_commission_7rate']), 6))
        if stock_info['hold_num'] * stock_info['askPrice'][0] * 100 + self.min_bid_money < self.max_bid_money:
            if len(stock_info['askPrice']) == 0 or stock_info['askPrice'][0] > appraisal:
                self.sell_queue.remove_stock(stock_code)
            premium_threshold = data_loader.get_premium(index_info['increase_rate'], self.base_premium_threshold)
            first_premium = Decimal(round((appraisal - Decimal(stock_info['askPrice'][0])) / Decimal(appraisal) * 100, 4))
            if first_premium > premium_threshold and stock_info["askVol"][0] * stock_info["askPrice"][0] * 100 > self.min_bid_money:
                # 这里的premium是一个权重，
                self.sell_queue.upsert_stock(stock_code, stock_info["name"], stock_info["askVol"][0], stock_info["askPrice"][0], (first_premium - (premium_threshold - Decimal(self.base_premium_threshold))), appraisal, date_utils.get_current_millisecond())
            else:
                self.sell_queue.remove_stock(stock_code)
        else:
            self.sell_queue.remove_stock(stock_code)

        # 计算委买的，买一大于200元、持有数量大于0的，才能进入队列
        buy_premium = Decimal(round((Decimal(stock_info['bidPrice'][0]) - appraisal) / Decimal(appraisal) * Decimal(100), 4))
        premium_threshold = data_loader.get_sell_premium(index_info['increase_rate'])
        if (len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] > 0
                and stock_info["bidVol"][0] * stock_info["bidPrice"][0] * 100 > 200 and stock_info['hold_num'] > 0):
            self.buy_queue.upsert_stock(stock_code, stock_info["name"], stock_info["bidVol"][0], stock_info["bidPrice"][0], buy_premium - premium_threshold, appraisal, date_utils.get_current_millisecond())
        else:
            self.buy_queue.remove_stock(stock_code)
        # 每分钟print一次信息
        if utils.should_print(60):
            first_sell_queue = self.sell_queue.head
            first_buy_queue = self.buy_queue.head
            if first_buy_queue is not None and first_sell_queue is not None:
                logger.info(
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\r\n"
                    f"-sell队列{first_sell_queue.name}-{first_sell_queue.code},估值: {first_sell_queue.appraisal}, 卖一报价: {round(first_sell_queue.price, 4)}, 折价率: {round((Decimal(first_sell_queue.appraisal) - Decimal(first_sell_queue.price)) / Decimal(first_sell_queue.price) * Decimal(100), 4)}%, premium权重: {first_sell_queue.premium}, 金额 {round(first_sell_queue.price*first_sell_queue.quantity*100, 2)}\r\n"
                    f"-buy队列{first_buy_queue.name}-{first_buy_queue.code},估值: {first_buy_queue.appraisal}, 买一报价: {round(first_buy_queue.price, 4)}, 折价率: {round((Decimal(first_buy_queue.price) - Decimal(first_buy_queue.appraisal)) / Decimal(first_buy_queue.price) * Decimal(100), 4)}%, premium权重: {first_buy_queue.premium}, 金额 {round(first_buy_queue.price*first_buy_queue.quantity*100, 2)}\r\n")

    def to_buy(self, stock_code, limit_price, appraisal):
        # 卖不用管，买需要加锁，防止重复购买
        lock = self._get_lock(stock_code)
        lock.acquire()
        try:
            # 当卖盘不为空，并且卖1出价小于估值时，进一步再判断溢价空间
            stock_info = self.inner_stock_infos[stock_code]
            index_info = self.target_index_infos[stock_info['target_index']]
            if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0]:
                bid_price = 0
                bid_num = 0
                bid_money = 0
                premium_threshold = data_loader.get_premium(index_info['increase_rate'], self.base_premium_threshold)
                premium = 0
                first_premium = 0
                hold_num = stock_info['hold_num']
                asset = self.traderService.get_asset()
                # 已经持仓的金额
                holding_money = round(stock_info['hold_num'] * 100 * Decimal(stock_info['askPrice'][0]), 2)
                max_able_bid_money = Decimal(self.max_bid_money) - Decimal(holding_money)

                if asset.cash <= max_able_bid_money:
                    max_able_bid_money = asset.cash

                for i, price in enumerate(stock_info['askPrice']):
                    if price > limit_price:
                        continue
                    #premium = round((appraisal - Decimal(price)) / Decimal(appraisal) * 100, 4)
                    # if premium >= premium_threshold and bid_money <= max_able_bid_money:
                    if bid_money <= max_able_bid_money:
                        bid_price = round(price, 6)
                        bid_num += stock_info['askVol'][i]
                        bid_money += bid_price * bid_num * 100
                        # 如果超过了最大单笔限上额，减去一点
                        if bid_money > max_able_bid_money:
                            bid_num -= Decimal(math.ceil((Decimal(bid_money) - Decimal(max_able_bid_money)) / Decimal(bid_price) / Decimal(100)))

                # 已经持有的够多了，没有再买的空间了
                if max_able_bid_money < self.min_bid_money:
                    return
                if bid_money == 0:
                    return
                # 可买的数量太少也放弃出价
                if bid_money < self.min_bid_money:
                    logger.info(f"{stock_info['name']}, {stock_info['code']}, 可买数量太少, {bid_money} < {self.min_bid_money}")
                    return

                if bid_num > 0 and bid_price > 0:
                    # 下单
                    remark = f"买入日志: 买入{stock_code}, {datetime.now().strftime('%Y-%m-%d %H:%M:%S')},折价率: {round((appraisal - Decimal(stock_info['askPrice'][0])) / Decimal(stock_info['askPrice'][0]) * Decimal(100), 4)}%，" \
                             f"估值{appraisal},报价{bid_price},{bid_num}手, 目前卖盘{stock_info['askPrice']},{stock_info['askVol']}, 指数{index_info}, 当前持有{hold_num}"
                    logger.info(remark)
                    order_id = self.traderService.async_buy(stock_code, bid_price, bid_num, "折价策略", self.inner_stock_infos, hold_num)
                    if order_id:
                        logger.info(f"order_id: {order_id}")
                        # buy加锁了，睡眠性能就相当差了，解除sleep看看
                        # time.sleep(1)
                        order = self.traderService.query_by_order_id(int(order_id))
                        if not order:
                            # 撤单
                            self.traderService.cancel(order_id)
                        else:
                            if order.order_status != xtconstant.ORDER_SUCCEEDED:
                                # 撤单
                                self.traderService.cancel(order_id)
                                strategy_record.add(self.db, order_id, "折价策略", stock_code, order.traded_price,
                                                    order.traded_volume, index_info['current'], remark, 400)
                            elif order.traded_volume > 0:
                                strategy_record.add(self.db, order_id, "折价策略", stock_code, order.traded_price,
                                                    order.traded_volume, index_info['current'], remark, 300)
                        # 更新持有信息
                        data_loader.fresh_holding(self.inner_stock_infos, self.traderService.get_holding())
                    else:
                        logger.error("下单失败")
                    return
        finally:
            lock.release()

    def to_sell(self, stock_code, limit_price, appraisal):
        stock_info = self.inner_stock_infos[stock_code]
        index_info = self.target_index_infos[stock_info['target_index']]
        if len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] >= appraisal and stock_info['hold_num'] > 0:
            sell_price = stock_info['bidPrice'][0]
            sell_num = 0
            total_money = 0
            premium_threshold = data_loader.get_sell_premium(index_info['increase_rate'])
            premium = 0
            for i, price in enumerate(stock_info['bidPrice']):
                if price < limit_price:
                    continue
                if price >= limit_price:
                    premium = round((Decimal(price) - appraisal) / Decimal(appraisal) * 100, 4)
                    # if premium < premium_threshold:
                    #     break
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
                remark = f"卖出日志: 指数rate:{index_info['increase_rate']}, premium: {premium}, premium_threshold: {premium_threshold}, 卖出{stock_code}, {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}," \
                         f"估值{appraisal},报价{sell_price},{sell_num}手, 目前买盘{stock_info['bidPrice']},{stock_info['bidVol']}, 指数{index_info}"
                logger.info(remark)
                order_id = self.traderService.sync_sell(stock_code, sell_price, sell_num, "折价策略",
                                                        self.inner_stock_infos)
                print(f"卖出orderid: {order_id}")
                order = self.traderService.query_by_order_id(int(order_id))
                print(f"卖出结果: {order}")
                strategy_record.add(self.db, order_id, "折价策略", stock_code, sell_price,
                                    sell_num*100, index_info['current'], remark, 200)
                # 更新持有信息
                data_loader.fresh_holding(self.inner_stock_infos, self.traderService.get_holding())

    def to_cancel(self, sell_stock_code, buy_stock_code):
        hangings = self.traderService.get_hanging()
        for item in hangings:
            if item.stock_code == sell_stock_code or item.stock_code == buy_stock_code:
                print(f"撤销委托, order_id: {item.order_id}, 撤销结果: {self.traderService.cancel(item.order_id)}")
                # print(item.order_time)
                # print(item.order_status)