# coding=utf-8
import json

from xtquant import xtdata
import logging
import threading
import time
from datetime import datetime
import helper.data_loader as data_loader
from service import stock_queue, trader_service as trader_services
from helper.time_utils import get_datetime, get_time
from helper import utils, date_utils, notifier
from service.watchdog_service import WatchdogService

logger = logging.getLogger(__name__)


print_count_index = 0
rest_index_push_count = 0
class Strategy2:
    def __init__(self, db, trader_service, platform, cookie, realtime_iopv_infos):
        self.frozen_amount = 0
        self.bought_list = {}
        self.stock_list = []
        # 等待被初始化的全局场内基金
        self.inner_stock_infos = {}
        # 等待被初始化的全局指数
        self.target_index_infos = {}

        self.realtime_iopv_infos = realtime_iopv_infos
        # 单个股票最大可以持仓多少钱
        self.max_single_amount = 2200
        # 每次出价最低多少钱
        self.min_bid_amount = 100

        self.base_premium_threshold = 0.3
        self.strategy_name = "ETF策略"
        self.strategy_etf_type = "etf"
        self.platform = platform
        self.completed_loading = False
        # 上一个交易日
        self.yesterday = data_loader.get_previous_date()

        self.db = db
        self.locks = {}
        self.trader_service = trader_service
        self.premium_manager = stock_queue.PremiumStrategyManager(self.base_premium_threshold, self.min_bid_amount, self.max_single_amount)
        self.trader_strategy_service = trader_services.TraderStrategyService(platform, self.min_bid_amount, self.max_single_amount, self.frozen_amount, trader_service,self.strategy_name)
        self.watchdog = WatchdogService()
        self.spider_cookie = cookie

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    # 启动策略
    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos, self.strategy_etf_type)
        from db import index_daily_history
        for code, stock_info in self.inner_stock_infos.items():
            stock_info['target_index'] = code
            penalty_rate = index_daily_history.get_index_penalty_rate(self.db, code, self.yesterday)
            self.target_index_infos[code] = {
                'relation': [code],
                'penalty_rate': penalty_rate,
                'status': True,
                'index_total_market_value': 0.0,
                'increase_rate': 0
            }
        data_loader.fresh_holding(self.inner_stock_infos, self.target_index_infos, self.trader_service.get_holding())

        group_codes = []
        for stock_code in self.inner_stock_infos:
            group_codes.append(stock_code)

        subscribe_id = xtdata.subscribe_whole_quote(group_codes, callback=self.handler)
        logging.info(f"subscribe successful: {subscribe_id}")
        self.watchdog.register("s2_stock", 180, "策略2-ETF行情")

        time.sleep(5)
        self.watchdog.start()
        self.completed_loading = True
        threading.Thread(target=data_loader.interval_fresh_holding, args=(self.inner_stock_infos, self.target_index_infos, self.trader_service)).start()

    def handler(self, msgs):
        global print_count_index
        t0 = time.perf_counter()
        try:
            self.watchdog.feed(f"s2_stock")
            target_index = None
            for code in list(msgs):
                self.handle_stock(msgs[code], code)
                if not utils.is_normal_trading_hours():
                    # print("未到开盘时间或已收盘")
                    return
                # begin to analysis data
                stock_info = self.inner_stock_infos[code]
                index_info = self.target_index_infos[stock_info['target_index']]
                if not stock_info['status']:
                    if self.completed_loading:
                        logger.warning(f"状态未就绪:")
                        logger.warning(stock_info)
                    continue

                # 如果更新时间超过2秒就不处理了
                if (get_time() - stock_info['timestamp'] > 2) and (get_time() - self.realtime_iopv_infos[utils.purified_code(code)]['timestamp'] > 2):
                    self.premium_manager.sell_queue.remove_stock(code)
                    # 超过10秒必然是异常，需要提示出来
                    if get_time() - self.realtime_iopv_infos[utils.purified_code(code)]['timestamp'] >= 15:
                        logger.error(f"etf更新时间异常，{get_time() - self.realtime_iopv_infos[utils.purified_code(code)]['timestamp']}秒未更新")
                        self.premium_manager.buy_queue.remove_stock(code)
                        logger.info(self.realtime_iopv_infos[utils.purified_code(code)])
                    if get_time() - stock_info['timestamp'] >= 60:
                        logger.error(f"stock{code} 更新时间异常，{get_time() - stock_info['timestamp']}秒未更新")
                        self.premium_manager.buy_queue.remove_stock(code)
                        logger.info(stock_info)
                    continue
                if stock_info['last_net_worth_date'] != self.yesterday:
                    logger.warning(f"last_net_worth_date异常: {stock_info['last_net_worth_date']} - {self.yesterday}")
                    continue
                # maintain a premium queue
                stock_info['last_net_worth'] = float(stock_info['last_net_worth'])

                self.premium_manager.update(code, stock_info, index_info, self.realtime_iopv_infos[utils.purified_code(code)])
                # it's the time to design trading part
                first_buy_queue_node = self.premium_manager.buy_queue.head
                first_sell_queue_node = self.premium_manager.sell_queue.head
                # check whether is can be sold
                if first_buy_queue_node is not None and first_buy_queue_node.code == code and first_buy_queue_node.premium >= 0:
                    logger.info(f"prepare to sell {code}")
                    self.premium_manager.buy_queue.remove_stock(code)
                    self.trader_strategy_service.to_sell(self.inner_stock_infos, self.target_index_infos, code, first_buy_queue_node.price,
                                                         first_buy_queue_node.appraisal, True)
                    logger.info(f"origin_tick: {msgs[code]}")
                    continue

                if not self.completed_loading:
                    continue
                # handle trading about buying
                asset = self.trader_service.get_asset()
                # whether money is enough
                if asset.cash - self.frozen_amount >= self.min_bid_amount:
                    if first_sell_queue_node is not None and first_sell_queue_node.code == code and first_sell_queue_node.premium >= 0:
                        logger.info(f"prepare to buy {code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                        self.premium_manager.sell_queue.remove_stock(code)
                        self.trader_strategy_service.to_buy(self.inner_stock_infos, self.target_index_infos, code, first_sell_queue_node.price,
                                                             first_sell_queue_node.appraisal, True)
                        logger.info(f"origin_tick: {msgs[code]}")
                    return
                else:
                    ## 如果钱不够了，买卖队列进行匹配，如果先卖后买有机会 then do it
                    # 如果买卖队列没有，无法匹配直接结束
                    if first_sell_queue_node is None or first_buy_queue_node is None:
                        continue
                    # 必须更新到自己的时候才能进行决策，否则old data可能已经失效了
                    if first_sell_queue_node.code != code and first_buy_queue_node.code != code:
                        continue
                    # 需要保证买卖队列的数据都是最新的
                    if (date_utils.get_current_millisecond() - first_sell_queue_node.update_time) / 1000 > 2:
                        self.premium_manager.sell_queue.remove_stock(first_sell_queue_node.code)
                        continue
                    if (date_utils.get_current_millisecond() - first_buy_queue_node.update_time) / 1000 > 2:
                        self.premium_manager.buy_queue.remove_stock(first_buy_queue_node.code)
                        continue
                    if ((first_sell_queue_node.premium > abs(first_buy_queue_node.premium) + self.base_premium_threshold) and
                            (first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] * first_buy_queue_node.price) and
                            self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] > 0 and first_buy_queue_node.premium > -self.base_premium_threshold):
                        # 操作之前，先从队列中移出去
                        self.premium_manager.buy_queue.remove_stock(first_buy_queue_node.code)
                        self.premium_manager.sell_queue.remove_stock(first_sell_queue_node.code)
                        # 先卖、后买、最后如果没有买成功取消委托(买和卖的都取消)
                        logger.info(f"队列策略[先卖后买]触发: {code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\r\n"
                                    f"{first_buy_queue_node.code}卖出, price:{round(first_buy_queue_node.price, 4)} quantity:{first_buy_queue_node.quantity} "
                                    f"appraisal:{round(first_buy_queue_node.appraisal, 5)} premium差:{round(first_buy_queue_node.premium, 4)};"
                                    f"{first_sell_queue_node.code}买入,price:{round(first_sell_queue_node.price, 4)} quantity:{first_sell_queue_node.quantity} "
                                    f"appraisal:{round(first_sell_queue_node.appraisal, 5)} premium差:{round(first_sell_queue_node.premium, 4)};")
                        self.trader_strategy_service.sell_then_buy(self.inner_stock_infos, self.target_index_infos, first_buy_queue_node, first_sell_queue_node)
                        logger.info(f"origin_tick: {msgs[code]}")
        except Exception as e:
            logger.exception(f"Stock handler CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handler中发生致命错误: {str(e)[:200]},\n请立即处理")
        finally:
            print_count_index += 1
            if print_count_index % 1011 == 0:
                print_count_index = 0
                thread_id = threading.get_ident()
                thread_name = threading.current_thread().name
                logging.info(f"Handler Thread: {thread_name} (ID: {thread_id})")
                logging.info(f'{datetime.now()} main函数运行耗时 {(time.perf_counter() - t0) * 1000:.3f} ms, 处理订阅任务数量: {len(msgs)}个')

    def handle_stock(self, stock_tick, stock_code):
        self.inner_stock_infos[stock_code].update({
            'timestamp': stock_tick['time'] / 1000,
            'time': datetime.fromtimestamp(stock_tick['time'] / 1000).strftime('%H:%M:%S'),
            'askPrice': stock_tick['askPrice'],
            'askVol': stock_tick['askVol'],
            'bidPrice': stock_tick['bidPrice'],
            'bidVol': stock_tick['bidVol'],
            'data': stock_tick,
            'status': True,
        })
