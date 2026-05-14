# coding=utf-8
from datetime import datetime
import json
import math
from decimal import Decimal
from time import sleep

from xtquant import xtdata, xtconstant
import helper.data_loader as data_loader
from helper import utils, spider, date_utils, notifier
import logging
import time
import threading
from service import stock_queue, adaptive_task_processor, trader_service as trader_services
from helper.time_utils import get_time, get_datetime
from service.watchdog_service import WatchdogService

logger = logging.getLogger(__name__)
rest_index_push_count = 0

class Strategy1:
    def __init__(self, db, trader_service, platform, cookie):
        # 预留的钱雷打不动，用来提出
        self.frozen_money = 0
        # 等待被初始化的全局场内基金
        self.inner_stock_infos = {}
        # 等待被初始化的全局指数
        self.target_index_infos = {}
        # 上一个交易日
        self.yesterday = data_loader.get_previous_date()
        # 单笔最大买入金额
        self.max_bid_money = 14000
        self.min_bid_money = 500
        self.base_premium_threshold = 0.25
        self.db = db
        self.trader_service = trader_service
        self.is_normal_trading_hours = False
        self.platform = platform
        self.completed_loading = False
        self.last_stock_pulse_time = 0
        self.last_index_pulse_time = 0
        # 多线程请求时，最多5个线程
        self.semaphore = threading.Semaphore(1)
        self.sell_queue = stock_queue.StockQueue()
        self.buy_queue = stock_queue.StockQueue()
        self.locks = {}
        self.processor = adaptive_task_processor.AdaptiveTaskProcessor()
        self.strategy_etf_type = "lof"
        self.strategy_name = "折价策略"
        self.trader_strategy_service = trader_services.TraderStrategyService(platform, self.min_bid_money, self.max_bid_money, self.frozen_money, trader_service,self.strategy_name)
        self.watchdog = WatchdogService()
        self.spider_cookie = cookie
    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos, self.strategy_etf_type)
        data_loader.load_target_index(self.db, self.inner_stock_infos, self.target_index_infos, self.yesterday)
        data_loader.fresh_holding(self.inner_stock_infos, self.target_index_infos, self.trader_service.get_holding())

        SId1 = xtdata.subscribe_whole_quote(data_loader.get_all_inner_stocks_code(self.db, self.strategy_etf_type), callback=self.stock_handler)
        SId2 = xtdata.subscribe_whole_quote(data_loader.get_all_target_index_code(self.inner_stock_infos), callback=self.index_handler)
        self.watchdog.register("s1_stock", 180, "策略1-stock行情")
        self.watchdog.register("s1_index", 180, "策略1-index行情")
        self.watchdog.start()

        logger.info(f"策略1启动，订阅成功: SId1-{SId1}, SId2-{SId2}\r")

        time.sleep(5)
        # 5秒后，开始用另一种方式监听没有订阅到的指数
        logger.info(f"loading rest index...")
        rest_index_codes = data_loader.get_rest_index(self.target_index_infos)
        logger.info(f"total target index nums: {len(self.target_index_infos)}")
        logger.info(f"rest_index_codes nums: {len(rest_index_codes)}, {rest_index_codes}")
        # 异步多线程通过第三方订阅没有检测到的指数信息
        self.subscribe_rest_index_stock(rest_index_codes)
        time.sleep(10)
        self.completed_loading = True
        # 开个线程定时刷新持仓
        threading.Thread(target=data_loader.interval_fresh_holding, args=(self.inner_stock_infos, self.target_index_infos, self.trader_service)).start()

    def stock_handler(self, msgs):
        start_time = time.perf_counter()
        try:
            self.watchdog.feed("s1_stock")
            self.is_normal_trading_hours = utils.is_normal_trading_hours()
            for code in msgs:
                # logger.info(f"订阅消息: stock-  {msgs[code]}")
                # 港股今天不交易，排除港股的数据
                # if '港' in self.inner_stock_infos[code]['name'] or 'H' in self.inner_stock_infos[code]['name'] or 'h' in self.inner_stock_infos[code]['name']:
                    #logger.info(self.inner_stock_infos[code])
                    # continue
                self.inner_stock_infos[code].update({
                    'time': datetime.fromtimestamp(msgs[code]['time'] / 1000).strftime('%H:%M:%S'),
                    'timestamp': msgs[code]['time'] / 1000,
                    'askPrice': msgs[code]['askPrice'],
                    'askVol': msgs[code]['askVol'],
                    'bidPrice': msgs[code]['bidPrice'],
                    'bidVol': msgs[code]['bidVol'],
                    'data': msgs[code],
                    'status': True,
                })
                # logger.info(f"stock_handler-{inner_stock_infos[code]}")
                # 分析关联的code
                #self.processor.submit_task(self.analysis_and_decision_mking, code)
                self.analysis_and_decision_mking(code, msgs[code])
        except Exception as e:
            logger.exception(f"Stock handler CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, stock_handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            total_time = time.perf_counter()
            total_consume = total_time - start_time
            if total_consume >= 0.5:
                logger.info(f"stock_handler 耗时统计: 总耗时: {total_consume:.3f}")

    def index_handler(self, msgs):
        try:
            self.watchdog.feed("s1_index")
            self.is_normal_trading_hours = utils.is_normal_trading_hours()
            for code in msgs:
                # logger.info(f"订阅消息: index-{code},  {msgs[code]}")
                if msgs[code]['lastClose'] == 0:
                    continue
                if datetime.fromtimestamp(msgs[code]['time'] / 1000).strftime('%H:%M:%S') == "00:00:00":
                    if self.target_index_infos[utils.purified_code(code)]['status']:
                        self.target_index_infos[utils.purified_code(code)].update({'status': False})
                    continue
                self.target_index_infos[utils.purified_code(code)].update({
                    'time': datetime.fromtimestamp(msgs[code]['time'] / 1000).strftime('%H:%M:%S'),
                    'timestamp': msgs[code]['time'] / 1000,
                    'start': msgs[code]['lastClose'],
                    'current': msgs[code]['lastPrice'],
                    'increase_rate': Decimal(
                        round((msgs[code]['lastPrice'] - msgs[code]['lastClose']) / msgs[code]['lastClose'], 6)),
                    'data': msgs[code],
                    'status': True,
                })
        except Exception as e:
            logger.exception(f"Stock Index handler CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, index_handler中发生致命错误: {str(e)[:200]},\n请立即处理")

    def subscribe_rest_index_stock(self, rest_index_codes):
        for index_code in rest_index_codes:
            threading.Thread(target=spider.stream_listener, args=(index_code, self.spider_cookie, self.subscribe_detail_index_stock)).start()
            time.sleep(0.5)

    def subscribe_detail_index_stock(self, line, index_code):
        global rest_index_push_count
        try:
            self.is_normal_trading_hours = utils.is_normal_trading_hours()
            data = json.loads(line.replace('data: ', ''))
            if data['data'] == "null" or data['data'] is None:
                # logger.info(f"{index_code}: {data['data']}")
                return
            # 记录last day数据
            if 'f60' in data['data']:
                self.target_index_infos[index_code].update({'start': Decimal(data['data']['f60'])})
            if 'f43' not in data['data']:
                # logger.warning(f"{index_code} [subscribe_detail_index_stock] 发生错误，关键ke数据不存在- {line}")
                return
            current_time = 0
            if 'f86' in data['data']:
                current_time = data['data']['f86']
            resp = {'current_index': data['data']['f43']}
            current_time_formate = datetime.fromtimestamp(current_time).strftime('%H:%M:%S')
            current_index = Decimal(resp['current_index'])
            if current_index <= 0:
                return
            self.target_index_infos[index_code].update({
                # 只有从这里更新的指数数据有这个key，防止连接中断后依据死数据做决策
                'time': current_time_formate,
                'timestamp': current_time,
                'current': resp['current_index'],
                'increase_rate': Decimal(
                    round((Decimal(resp['current_index']) - self.target_index_infos[index_code]['start']) / self.target_index_infos[index_code]['start'], 6)),
                'data': data['data'],
                'status': True,
            })
            rest_index_push_count += 1
            if rest_index_push_count % 250 == 0:
                rest_index_push_count = 0
                logger.info(f"subscribe_detail_index_stock: [{index_code}]-{self.target_index_infos[index_code]}")
        except IOError as e:
            logger.error(e)
            return

    def analysis_and_decision_mking(self, stock_code, origin_tick = None):
        start_time = time.perf_counter()
        try:
            if not self.is_normal_trading_hours:
                # print("未到开盘时间或已收盘.")
                return None
            step0 = time.perf_counter()
            # 有可能会执行超时，在这里超时直接阻断
            if step0 - start_time >= 1:
                logger.warning(f"阻断，执行时间过长, step0 执行时间{(step0 - start_time):.3f}s")
                return None

            stock_info = self.inner_stock_infos[stock_code]
            index_info = self.target_index_infos[stock_info['target_index']]
            # 双方未就绪，不处理
            if index_info['status'] == False or stock_info['status'] == False:
                if self.completed_loading:
                    logger.warning(f"状态未就绪:")
                    logger.warning(stock_info)
                    logger.warning(index_info)
                return None

            if stock_info['last_net_worth_date'] != self.yesterday:
                # 白天可以用，晚上就不行了
                logger.warning(f"last_net_worth_date异常: {stock_info['last_net_worth_date']} - {self.yesterday}")
                return None

            if (get_time() - index_info['timestamp'] > 8) or (get_time() - stock_info['timestamp'] > 8):
                self.sell_queue.remove_stock(stock_code)
                # self.buy_queue.remove_stock(stock_code)
                # 超过10秒必然是异常，需要提示出来
                if get_time() - index_info['timestamp'] >= 60:
                    logger.error(f"index{stock_info['target_index']} 更新时间异常，{get_time() - index_info['timestamp']}秒未更新")
                    self.buy_queue.remove_stock(stock_code)
                    logger.info(index_info)
                if get_time() - stock_info['timestamp'] >= 60:
                    logger.error(f"stock{stock_code} 更新时间异常，{get_time() - stock_info['timestamp']}秒未更新")
                    self.buy_queue.remove_stock(stock_code)
                    logger.info(stock_info)
                return None
            # 维护两个队列
            self.maintain_premium_queues(stock_code, stock_info, index_info)
            # 买一队列中，只有premium大于0就会立即被卖，所以只需要取队列头的第一个数据
            first_buy_queue_node = self.buy_queue.head
            first_sell_queue_node = self.sell_queue.head
            if first_buy_queue_node is not None and first_buy_queue_node.code == stock_code and first_buy_queue_node.premium > 0:
                logger.info(f"prepare to sell {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                self.buy_queue.remove_stock(stock_code)
                # self.to_sell(stock_code, first_buy_queue_node.price, first_buy_queue_node.appraisal)
                self.trader_strategy_service.to_sell(self.inner_stock_infos, self.target_index_infos, stock_code,
                                                     float(first_buy_queue_node.price), float(first_buy_queue_node.appraisal))
                logger.info(f"origin_tick: {origin_tick}")
                # 如果当前stock_code能卖，就一定不会有买入机会，直接结束
                return None
            if not self.completed_loading:
                return None
            # 如果钱够，遇到好的委卖数据果断买入
            asset = self.trader_service.get_asset()
            if asset.cash - self.frozen_money >= self.min_bid_money:
                if first_sell_queue_node is not None and first_sell_queue_node.code == stock_code and first_sell_queue_node.premium > 0:
                    logger.info(f"prepare to buy {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                    self.sell_queue.remove_stock(stock_code)
                    self.trader_strategy_service.to_buy(self.inner_stock_infos, self.target_index_infos, stock_code,
                                                        float(first_sell_queue_node.price), float(first_sell_queue_node.appraisal))
                    logger.info(f"origin_tick: {origin_tick}")
                    # self.to_buy(stock_code, first_sell_queue_node.price, first_sell_queue_node.appraisal)
                return True
            else:
                ## 如果钱不够了，买卖队列进行匹配，如果先卖后买有利 then do it
                # 如果买卖队列没有，无法匹配直接结束
                if first_sell_queue_node is None or first_buy_queue_node is None:
                    return None
                # 必须更新到自己的时候才能进行决策，否则old data可能已经失效了
                if first_sell_queue_node.code != stock_code and first_buy_queue_node.code != stock_code:
                    return None
                # 需要保证买卖队列的数据都是最新的
                if (date_utils.get_current_millisecond() - first_sell_queue_node.update_time) / 1000 > 1:
                    self.sell_queue.remove_stock(first_sell_queue_node.code)
                    return None
                if (date_utils.get_current_millisecond() - first_buy_queue_node.update_time) / 1000 > 1:
                    self.sell_queue.remove_stock(first_buy_queue_node.code)
                    return None
                # 买卖队列有利，并且可买的两完全覆盖卖的量,sell是正数，buy是负数，
                # 防止卖了买不进去，卖的premium差必须小于基准值
                # logger.info(f"[先卖后买]日志: 第一个条件[{(first_sell_queue_node.premium > Decimal(abs(first_buy_queue_node.premium)) + Decimal(self.base_premium_threshold))}]"
                #       f"第二个条件[{(first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] * first_buy_queue_node.price)}]"
                #       f"第三个条件[{self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] > 0}, {self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num']}]"
                #       f"第四个条件[{first_buy_queue_node.premium > -self.base_premium_threshold}]"
                #       f"一 {first_sell_queue_node.premium} > {abs(first_buy_queue_node.premium)} + {Decimal(self.base_premium_threshold)}"
                #       f"二 {first_sell_queue_node.quantity} * {first_sell_queue_node.price} >= {self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num']} * {first_buy_queue_node.price}"
                #       f"四 {first_buy_queue_node.premium} > {-self.base_premium_threshold}")
                if ((first_sell_queue_node.premium > Decimal(abs(first_buy_queue_node.premium)) + Decimal(self.base_premium_threshold)) and
                        (first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] * first_buy_queue_node.price) and
                        self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] > 0 and first_buy_queue_node.premium > -self.base_premium_threshold):
                    # 操作之前，先从队列中移出去
                    self.buy_queue.remove_stock(first_buy_queue_node.code)
                    self.sell_queue.remove_stock(first_sell_queue_node.code)
                    # 先卖、后买、最后如果没有买成功取消委托(买和卖的都取消)
                    logger.info(f"队列策略[先卖后买]触发: {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\r\n"
                          f" {first_buy_queue_node.code}卖出, price:{round(first_buy_queue_node.price, 4)} quantity:{first_buy_queue_node.quantity} appraisal:{round(first_buy_queue_node.appraisal, 5)} premium差:{round(first_buy_queue_node.premium, 4)};"
                          f"   {first_sell_queue_node.code}买入,price:{round(first_sell_queue_node.price, 4)} quantity:{first_sell_queue_node.quantity} appraisal:{round(first_sell_queue_node.appraisal, 5)} premium差:{round(first_sell_queue_node.premium, 4)};")
                    first_buy_queue_node.premium = float(first_buy_queue_node.premium)
                    first_sell_queue_node.premium = float(first_sell_queue_node.premium)
                    self.trader_strategy_service.sell_then_buy(self.inner_stock_infos, self.target_index_infos, first_buy_queue_node, first_sell_queue_node)
                    logger.info(f"origin_tick: {origin_tick}")
                    return True
        except Exception as e:
            logger.exception(f"analysis_and_decision_mking CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            total_time = time.perf_counter()
            total_consume = total_time - start_time
            if total_consume >= 1:
                logger.info(f"耗时统计: 总耗时: {total_consume:.3f}")

    def maintain_premium_queues(self, stock_code, stock_info, index_info):
        # 计算的委卖的
        # premium符合要求，并且委卖大于最小买入金额，并且已经持有的金额不超过最大限制，维护到双向链表队列中
        appraisal = Decimal(round(stock_info['last_net_worth'] * (Decimal(1) + index_info['increase_rate']) * (
                    Decimal(1) - stock_info['withdraw_commission_7rate']), 6))
        current_hold_val = float(stock_info['hold_num']) * stock_info['askPrice'][0] * 100
        index_unused_money_capacity = self.max_bid_money * 2 - index_info['index_total_market_value']
        # 逻辑：总持仓金额未超限、指数总持仓未超限
        if (current_hold_val + self.min_bid_money < self.max_bid_money) and (index_unused_money_capacity > self.min_bid_money):
            if len(stock_info['askPrice']) == 0 or stock_info['askPrice'][0] > appraisal:
                self.sell_queue.remove_stock(stock_code)
            overheating_penalty = data_loader.get_overheating_penalty(index_info['increase_rate'])
            history_penalty_rate = Decimal(index_info['penalty_rate'])
            premium_threshold = Decimal(self.base_premium_threshold) + overheating_penalty + history_penalty_rate

            first_premium = Decimal(round((appraisal - Decimal(stock_info['askPrice'][0])) / Decimal(appraisal) * 100, 4))
            if first_premium > 0 and stock_info["askVol"][0] * stock_info["askPrice"][0] * 100 > self.min_bid_money:
                # 这里的premium是一个权重，
                self.sell_queue.upsert_stock(stock_code, stock_info["name"], stock_info["askVol"][0], stock_info["askPrice"][0], first_premium - premium_threshold, first_premium, appraisal, history_penalty_rate, overheating_penalty, date_utils.get_current_millisecond())
            else:
                self.sell_queue.remove_stock(stock_code)
        else:
            self.sell_queue.remove_stock(stock_code)

        # 计算委买的，买一大于200元、持有数量大于0的，才能进入队列
        buy_premium = Decimal(round((Decimal(stock_info['bidPrice'][0]) - appraisal) / Decimal(appraisal) * Decimal(100), 4))
        buy_premium_threshold = data_loader.get_sell_premium(index_info['increase_rate'])
        if (len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] > 0
                and stock_info["bidVol"][0] * stock_info["bidPrice"][0] * 100 > 200 and stock_info['hold_can_use_num'] > 0):
            self.buy_queue.upsert_stock(stock_code, stock_info["name"], stock_info["bidVol"][0], stock_info["bidPrice"][0], buy_premium - buy_premium_threshold, buy_premium,appraisal, 0,0, date_utils.get_current_millisecond())
        else:
            self.buy_queue.remove_stock(stock_code)
        # 每分钟print一次信息
        if utils.should_print("strategy1", 60):
            logger.info("**********************打印队列数据情况 start **********************")
            first_sell_queue = self.sell_queue.head
            first_buy_queue = self.buy_queue.head
            logger.info(f"\r\n{stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            if first_sell_queue is not None:
                logger.info(
                    f"-sell队列{first_sell_queue.name}-{first_sell_queue.code},估值: {first_sell_queue.appraisal}, 卖一报价: {round(first_sell_queue.price, 4)}, 折价率: {round((Decimal(first_sell_queue.appraisal) - Decimal(first_sell_queue.price)) / Decimal(first_sell_queue.price) * Decimal(100), 4)}%, premium权重: {first_sell_queue.premium}, 金额 {round(first_sell_queue.price * first_sell_queue.quantity * 100, 2)}, 历史惩罚: {first_sell_queue.history_penalty_rate}, 过热惩罚: {first_sell_queue.overheating_penalty}\r\n")
            if first_buy_queue is not None:
                logger.info(
                    f"-buy队列{first_buy_queue.name}-{first_buy_queue.code},估值: {first_buy_queue.appraisal}, 买一报价: {round(first_buy_queue.price, 4)}, 折价率: {round((Decimal(first_buy_queue.price) - Decimal(first_buy_queue.appraisal)) / Decimal(first_buy_queue.price) * Decimal(100), 4)}%, premium权重: {first_buy_queue.premium}, 金额 {round(first_buy_queue.price*first_buy_queue.quantity*100, 2)}\r\n")
            self.buy_queue.print_queue()
            logger.info("**********************打印队列数据情况 end **********************")
        # 快收盘的前几分钟，开始每10秒展示实际估值，手动查看是否有可以卖出的标的
        if utils.is_going_to_close() and utils.should_print("strategy1", 10):
            self.buy_queue.print_queue()
