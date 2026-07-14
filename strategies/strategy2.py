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
        # Global exchange-traded funds to be initialized
        self.inner_stock_infos = {}
        # Global indices to be initialized
        self.target_index_infos = {}

        self.realtime_iopv_infos = realtime_iopv_infos
        # Maximum position value per single stock
        self.max_single_amount = 2200
        # Minimum capital size per single order
        self.min_bid_amount = 1

        self.base_premium_threshold = 0.3
        self.strategy_name = "EtfStrategy"
        self.strategy_etf_type = "etf"
        self.platform = platform
        self.completed_loading = False
        # Previous trading day
        self.yesterday = data_loader.get_previous_date()

        self.db = db
        self.locks = {}
        self.trader_service = trader_service
        self.premium_manager = stock_queue.PremiumStrategyManager(self.base_premium_threshold, self.min_bid_amount, self.max_single_amount)
        self.trader_strategy_service = trader_services.TraderStrategyService(platform, self.min_bid_amount, self.max_single_amount, self.frozen_amount, trader_service,self.strategy_name)
        self.watchdog = WatchdogService()
        self.spider_cookie = cookie

    def _get_lock(self, stock_code):
        # Create a new lock for the stock_code if it does not exist
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    # Start strategy
    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos, self.strategy_etf_type)
        data_loader.load_target_index_for_etf(self.db, self.inner_stock_infos, self.target_index_infos, self.yesterday, self.strategy_etf_type)
        data_loader.fresh_holding(self.inner_stock_infos, self.target_index_infos, self.trader_service.get_holding())

        group_codes = []
        for stock_code in self.inner_stock_infos:
            group_codes.append(stock_code)

        subscribe_id = xtdata.subscribe_whole_quote(group_codes, callback=self.handler)
        logging.info(f"Subscription successful: {subscribe_id}")
        self.watchdog.register("s2_stock", 30, "策略2-ETF行情")

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
                    # print("Market closed or pre-open")
                    return
                # begin analysis
                stock_info = self.inner_stock_infos[code]
                index_info = self.target_index_infos[data_loader.get_group_code(stock_info['target_index'], code)]
                if not stock_info['status']:
                    if self.completed_loading:
                        logger.warning(f"State not ready:")
                        logger.warning(stock_info)
                    continue

                # Ignore updates with latency > 2 seconds
                if (get_time() - stock_info['timestamp'] > 2) and (get_time() - self.realtime_iopv_infos[utils.purified_code(code)]['timestamp'] > 2):
                    self.premium_manager.sell_queue.remove_stock(code)
                    # Latency > 10 seconds indicates anomaly; alert required
                    if get_time() - self.realtime_iopv_infos[utils.purified_code(code)]['timestamp'] >= 15:
                        logger.error(f"ETF update interval anomaly: no updates for {get_time() - self.realtime_iopv_infos[utils.purified_code(code)]['timestamp']} seconds")
                        self.premium_manager.buy_queue.remove_stock(code)
                        logger.info(self.realtime_iopv_infos[utils.purified_code(code)])
                    if get_time() - stock_info['timestamp'] >= 600:
                        logger.error(f"Stock {code} update interval anomaly: no updates for {get_time() - stock_info['timestamp']} seconds")
                        self.premium_manager.buy_queue.remove_stock(code)
                        # logger.info(stock_info)
                    continue
                if stock_info['last_net_worth_date'] != self.yesterday:
                    logger.warning(f"last_net_worth_date anomaly: {stock_info['last_net_worth_date']} - {self.yesterday}")
                    continue
                # maintain the premium queue
                stock_info['last_net_worth'] = float(stock_info['last_net_worth'])

                self.premium_manager.update(code, stock_info, index_info, self.realtime_iopv_infos[utils.purified_code(code)])
                # Evaluate trading execution
                first_buy_queue_node = self.premium_manager.buy_queue.head
                first_sell_queue_node = self.premium_manager.sell_queue.head
                # Evaluate sell execution
                if first_buy_queue_node is not None and first_buy_queue_node.code == code and first_buy_queue_node.premium >= 0:
                    logger.info(f"prepare to sell {code}")
                    self.premium_manager.buy_queue.remove_stock(code)
                    self.trader_strategy_service.to_sell(self.inner_stock_infos, self.target_index_infos, code, first_buy_queue_node.price,
                                                         first_buy_queue_node.appraisal, True)
                    logger.info(f"origin_tick: {msgs[code]}")
                    continue

                if not self.completed_loading:
                    continue
                # Evaluate buy execution
                asset = self.trader_service.get_asset()
                # Verify if cash is sufficient
                if asset.cash - self.frozen_amount >= self.min_bid_amount:
                    if first_sell_queue_node is not None and first_sell_queue_node.code == code and first_sell_queue_node.premium >= 0:
                        logger.info(f"prepare to buy {code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                        self.premium_manager.sell_queue.remove_stock(code)
                        self.trader_strategy_service.to_buy(self.inner_stock_infos, self.target_index_infos, code, first_sell_queue_node.price,
                                                             first_sell_queue_node.appraisal, True)
                        logger.info(f"origin_tick: {msgs[code]}")
                    return
                else:
                    ## If cash is insufficient, match buy and sell queues. If sell-then-buy is profitable, execute.
                    # Terminate if buy or sell queues are empty
                    if first_sell_queue_node is None or first_buy_queue_node is None:
                        continue
                    # Only evaluate when current stock updates; stale queue data may be invalid
                    if first_sell_queue_node.code != code and first_buy_queue_node.code != code:
                        continue
                    # Ensure queue records are up to date
                    if (date_utils.get_current_millisecond() - first_sell_queue_node.update_time) / 1000 > 2:
                        self.premium_manager.sell_queue.remove_stock(first_sell_queue_node.code)
                        continue
                    if (date_utils.get_current_millisecond() - first_buy_queue_node.update_time) / 1000 > 2:
                        self.premium_manager.buy_queue.remove_stock(first_buy_queue_node.code)
                        continue
                    if ((first_sell_queue_node.premium > abs(first_buy_queue_node.premium) + self.base_premium_threshold) and
                            (first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] * first_buy_queue_node.price) and
                            self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] > 0 and first_buy_queue_node.premium > -self.base_premium_threshold):
                        # Remove from queues prior to execution
                        self.premium_manager.buy_queue.remove_stock(first_buy_queue_node.code)
                        self.premium_manager.sell_queue.remove_stock(first_sell_queue_node.code)
                        # Execute sell-then-buy. Cancel unfilled orders for both directions if execution fails.
                        logger.info(f"Queue Strategy [Sell-Then-Buy] triggered: {code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\r\n"
                                    f" Sell Ticker: {first_buy_queue_node.code}, price: {round(first_buy_queue_node.price, 4)}, quantity: {first_buy_queue_node.quantity}, "
                                    f"appraisal: {round(first_buy_queue_node.appraisal, 5)}, premium diff: {round(first_buy_queue_node.premium, 4)};"
                                    f" Buy Ticker: {first_sell_queue_node.code}, price: {round(first_sell_queue_node.price, 4)}, quantity: {first_sell_queue_node.quantity}, "
                                    f"appraisal: {round(first_sell_queue_node.appraisal, 5)}, premium diff: {round(first_sell_queue_node.premium, 4)};")
                        self.trader_strategy_service.sell_then_buy(self.inner_stock_infos, self.target_index_infos, first_buy_queue_node, first_sell_queue_node)
                        logger.info(f"origin_tick: {msgs[code]}")
        except Exception as e:
            logger.exception(f"Stock handler CRASHED: {e}")
            notifier.send_telegram_alert("Alert", f"Strategy {self.strategy_name}, fatal error in handler: {str(e)[:200]},\nPlease check immediately.")
        finally:
            print_count_index += 1
            if print_count_index % 1011 == 0:
                print_count_index = 0
                thread_id = threading.get_ident()
                thread_name = threading.current_thread().name
                logging.info(f"Handler Thread: {thread_name} (ID: {thread_id})")
                logging.info(f'{datetime.now()} execution time: {(time.perf_counter() - t0) * 1000:.3f} ms, processed subscriptions: {len(msgs)}')

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
