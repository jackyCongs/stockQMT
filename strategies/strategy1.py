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
    def __init__(self, db, trader_service, platform, cookie, realtime_iopv_infos):
        # Fixed reserved capital (never used for trading)
        self.frozen_money = 0
        # Global exchange-traded funds to be initialized
        self.inner_stock_infos = {}
        # Global indices to be initialized
        self.target_index_infos = {}
        # Previous trading day
        self.yesterday = data_loader.get_previous_date()
        # Maximum capital size per single order
        self.max_bid_money = 7000
        self.min_bid_money = 500
        self.base_premium_threshold = 0.25
        self.db = db
        self.trader_service = trader_service
        self.is_normal_trading_hours = False
        self.platform = platform
        self.completed_loading = False
        self.last_stock_pulse_time = 0
        self.last_index_pulse_time = 0
        # Maximum of 5 concurrent execution threads
        self.semaphore = threading.Semaphore(1)
        self.sell_queue = stock_queue.StockQueue()
        self.buy_queue = stock_queue.StockQueue()
        self.locks = {}
        self.processor = adaptive_task_processor.AdaptiveTaskProcessor()
        self.strategy_etf_type = "lof"
        self.strategy_name = "DiscountStrategy"
        self.trader_strategy_service = trader_services.TraderStrategyService(platform, self.min_bid_money, self.max_bid_money, self.frozen_money, trader_service,self.strategy_name)
        self.watchdog = WatchdogService()
        self.spider_cookie = cookie
        self.realtime_iopv_infos = realtime_iopv_infos

    def _get_lock(self, stock_code):
        # Create a new lock for the stock_code if it does not exist
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos, self.strategy_etf_type)
        data_loader.load_target_index(self.db, self.inner_stock_infos, self.target_index_infos, self.yesterday)
        data_loader.fresh_holding(self.inner_stock_infos, self.target_index_infos, self.trader_service.get_holding())

        SId1 = xtdata.subscribe_whole_quote(data_loader.get_all_inner_stocks_code(self.db, self.strategy_etf_type), callback=self.stock_handler)
        SId2 = xtdata.subscribe_whole_quote(data_loader.get_all_target_index_code(self.inner_stock_infos), callback=self.index_handler)
        self.watchdog.register("s1_stock", 30, "策略1-stock行情")
        self.watchdog.register("s1_index", 30, "策略1-index行情")
        self.watchdog.start()

        logger.info(f"Strategy 1 started. Subscriptions: SId1={SId1}, SId2={SId2}\r")

        time.sleep(5)
        # After 5 seconds, query unsubscribed indices using alternative methods
        logger.info(f"loading rest index...")
        rest_index_codes = data_loader.get_rest_index(self.target_index_infos)
        logger.info(f"total target index nums: {len(self.target_index_infos)}")
        logger.info(f"rest_index_codes nums: {len(rest_index_codes)}, {rest_index_codes}")
        # Async multi-threaded fetch of missing index quotes via third-party feeds
        # self.subscribe_rest_index_stock(rest_index_codes)
        # time.sleep(10)
        self.completed_loading = True
        # Start a background thread to refresh positions periodically
        threading.Thread(target=data_loader.interval_fresh_holding, args=(self.inner_stock_infos, self.target_index_infos, self.trader_service)).start()

    def stock_handler(self, msgs):
        start_time = time.perf_counter()
        try:
            self.watchdog.feed("s1_stock")
            self.is_normal_trading_hours = utils.is_normal_trading_hours()
            for code in msgs:
                # logger.info(f"订阅消息: stock-  {msgs[code]}")
                # Exclude HK shares if HK market is closed
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
                # Analyze related code
                #self.processor.submit_task(self.analysis_and_decision_mking, code)
                self.analysis_and_decision_mking(code, msgs[code])
        except Exception as e:
            logger.exception(f"Stock handler CRASHED: {e}")
            notifier.send_telegram_alert("Alert", f"Strategy {self.strategy_name}, fatal error in stock_handler: {str(e)[:200]},\nPlease check immediately.")
        finally:
            total_time = time.perf_counter()
            total_consume = total_time - start_time
            if total_consume >= 0.5:
                logger.info(f"stock_handler duration stats: Total time: {total_consume:.3f}s")

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
                for stock_code in self.target_index_infos[utils.purified_code(code)]['relation']:
                    self.analysis_and_decision_mking(stock_code, msgs[code])
        except Exception as e:
            logger.exception(f"Stock Index handler CRASHED: {e}")
            notifier.send_telegram_alert("Alert", f"Strategy {self.strategy_name}, fatal error in index_handler: {str(e)[:200]},\nPlease check immediately.")

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
            # Record previous day's metrics
            if 'f60' in data['data']:
                self.target_index_infos[index_code].update({'start': Decimal(data['data']['f60'])})
            if 'f43' not in data['data']:
                # logger.warning(f"{index_code} [subscribe_detail_index_stock] Error: key data missing - {line}")
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
                # Only index quotes updated from here possess this key, preventing bad decisions on stale data
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
                # print("Market closed or pre-open.")
                return None
            step0 = time.perf_counter()
            # Intercept/Abort if execution duration exceeds the safety threshold
            if step0 - start_time >= 1:
                logger.warning(f"Aborted. Execution duration too long. step0 time: {(step0 - start_time):.3f}s")
                return None

            stock_info = self.inner_stock_infos[stock_code]
            index_info = self.target_index_infos[stock_info['target_index']]
            # Both sides not ready; skip
            if index_info['status'] == False or stock_info['status'] == False:
                if self.completed_loading:
                    pass
                    # logger.warning(f"状态未就绪:")
                    # logger.warning(stock_info)
                    # logger.warning(index_info)
                return None

            if stock_info['last_net_worth_date'] != self.yesterday:
                # Valid during active trading hours
                logger.warning(f"last_net_worth_date anomaly: {stock_info['last_net_worth_date']} - {self.yesterday}")
                return None

            if (get_time() - index_info['timestamp'] > 8) or (get_time() - stock_info['timestamp'] > 8):
                self.sell_queue.remove_stock(stock_code)
                # self.buy_queue.remove_stock(stock_code)
                # Delays > 10s indicate anomaly; alert required
                if get_time() - index_info['timestamp'] >= 60:
                    logger.error(f"Index {stock_info['target_index']} update interval anomaly: no updates for {get_time() - index_info['timestamp']} seconds")
                    self.buy_queue.remove_stock(stock_code)
                    logger.info(index_info)
                if get_time() - stock_info['timestamp'] >= 600:
                    logger.error(f"Stock {stock_code} update interval anomaly: no updates for {get_time() - stock_info['timestamp']} seconds")
                    self.buy_queue.remove_stock(stock_code)
                    # logger.info(stock_info)
                return None
            # Maintain the two queues
            self.maintain_premium_queues(stock_code, stock_info, index_info)
            # Best bids with premium > 0 will execute immediately; evaluate queue head first
            first_buy_queue_node = self.buy_queue.head
            first_sell_queue_node = self.sell_queue.head
            if first_buy_queue_node is not None and first_buy_queue_node.code == stock_code and first_buy_queue_node.premium > 0:
                logger.info(f"prepare to sell {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                self.buy_queue.remove_stock(stock_code)
                # self.to_sell(stock_code, first_buy_queue_node.price, first_buy_queue_node.appraisal)
                self.trader_strategy_service.to_sell(self.inner_stock_infos, self.target_index_infos, stock_code,
                                                     float(first_buy_queue_node.price), float(first_buy_queue_node.appraisal))
                logger.info(f"origin_tick: {origin_tick}")
                # If stock_code satisfies sell criteria, skip buy checks and terminate
                return None
            if not self.completed_loading:
                return None
            # If capital permits, execute buy on attractive ask quotes
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
                ## If capital is insufficient, match buy and sell queues. If sell-then-buy is profitable, execute.
                # Terminate if buy or sell queues are empty
                if first_sell_queue_node is None or first_buy_queue_node is None:
                    return None
                # Only evaluate when current stock updates; stale queue data may be invalid
                if first_sell_queue_node.code != stock_code and first_buy_queue_node.code != stock_code:
                    return None
                # Ensure queue records are up to date
                if (date_utils.get_current_millisecond() - first_sell_queue_node.update_time) / 1000 > 1:
                    self.sell_queue.remove_stock(first_sell_queue_node.code)
                    return None
                if (date_utils.get_current_millisecond() - first_buy_queue_node.update_time) / 1000 > 1:
                    self.sell_queue.remove_stock(first_buy_queue_node.code)
                    return None
                # Profit check: Verify buy queue headroom covers sell volume (sell positive, buy negative)
                # Ensure premium difference is below threshold to guarantee execution
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
                    # Remove from queues prior to execution
                    self.buy_queue.remove_stock(first_buy_queue_node.code)
                    self.sell_queue.remove_stock(first_sell_queue_node.code)
                    # Execute sell-then-buy. Cancel unfilled orders for both directions if execution fails.
                    logger.info(f"Queue Strategy [Sell-Then-Buy] triggered: {stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\r\n"
                          f" Sell Ticker: {first_buy_queue_node.code}, price: {round(first_buy_queue_node.price, 4)}, quantity: {first_buy_queue_node.quantity}, appraisal: {round(first_buy_queue_node.appraisal, 5)}, premium diff: {round(first_buy_queue_node.premium, 4)};"
                          f" Buy Ticker: {first_sell_queue_node.code}, price: {round(first_sell_queue_node.price, 4)}, quantity: {first_sell_queue_node.quantity}, appraisal: {round(first_sell_queue_node.appraisal, 5)}, premium diff: {round(first_sell_queue_node.premium, 4)};")
                    first_buy_queue_node.premium = float(first_buy_queue_node.premium)
                    first_sell_queue_node.premium = float(first_sell_queue_node.premium)
                    self.trader_strategy_service.sell_then_buy(self.inner_stock_infos, self.target_index_infos, first_buy_queue_node, first_sell_queue_node)
                    logger.info(f"origin_tick: {origin_tick}")
                    return True
        except Exception as e:
            logger.exception(f"analysis_and_decision_mking CRASHED: {e}")
            notifier.send_telegram_alert("Alert", f"Strategy {self.strategy_name}, fatal error in handler: {str(e)[:200]},\nPlease check immediately.")
        finally:
            total_time = time.perf_counter()
            total_consume = total_time - start_time
            if total_consume >= 1:
                logger.info(f"Performance stats: Total time: {total_consume:.3f}s")

    def maintain_premium_queues(self, stock_code, stock_info, index_info):
        # Compute ask parameters
        # If premium matches threshold, size is above min capital, and holdings are under limit, insert into queue
        estimate_position_rate = Decimal(0.93)
        if index_info['increase_rate'] < 0:
            estimate_position_rate = Decimal(0.95)
        appraisal = Decimal(round(stock_info['last_net_worth'] * (Decimal(1) + index_info['increase_rate'] * estimate_position_rate) * (
                    Decimal(1) - stock_info['withdraw_commission_7rate']), 6))
        current_hold_val = float(stock_info['hold_num']) * stock_info['askPrice'][0] * 100
        index_unused_money_capacity = self.max_bid_money * 2 - index_info['index_total_market_value']
        # Logic: Total holdings and target index exposure are below limits
        if (current_hold_val + self.min_bid_money < self.max_bid_money) and (index_unused_money_capacity > self.min_bid_money):
            if len(stock_info['askPrice']) == 0 or stock_info['askPrice'][0] > appraisal:
                self.sell_queue.remove_stock(stock_code)
            overheating_penalty = data_loader.get_overheating_penalty(index_info['increase_rate'])
            history_penalty_rate = Decimal(index_info['penalty_rate'])
            premium_threshold = Decimal(self.base_premium_threshold) + overheating_penalty + history_penalty_rate

            first_premium = Decimal(round((appraisal - Decimal(stock_info['askPrice'][0])) / Decimal(appraisal) * 100, 4))
            if first_premium > 0 and stock_info["askVol"][0] * stock_info["askPrice"][0] * 100 > self.min_bid_money:
                # Note: premium represents weight value
                self.sell_queue.upsert_stock(stock_code, stock_info["name"], stock_info["askVol"][0], stock_info["askPrice"][0], first_premium - premium_threshold, first_premium, appraisal, history_penalty_rate, overheating_penalty, date_utils.get_current_millisecond())
            else:
                self.sell_queue.remove_stock(stock_code)
        else:
            self.sell_queue.remove_stock(stock_code)


        estimate_position_rate = Decimal(0.93)
        if index_info['increase_rate'] > 0:
            estimate_position_rate = Decimal(0.95)
        appraisal = Decimal(round(stock_info['last_net_worth'] * (Decimal(1) + index_info['increase_rate'] * estimate_position_rate) * (
                    Decimal(1) - stock_info['withdraw_commission_7rate']), 6))
        # Compute bids; require bid value > 200 CNY and holding size > 0 to queue
        buy_premium = Decimal(round((Decimal(stock_info['bidPrice'][0]) - appraisal) / Decimal(appraisal) * Decimal(100), 4))
        buy_premium_threshold = data_loader.get_sell_premium(index_info['increase_rate'])
        if (len(stock_info['bidPrice']) > 0 and stock_info['bidPrice'][0] > 0
                and stock_info["bidVol"][0] * stock_info["bidPrice"][0] * 100 > 200 and stock_info['hold_can_use_num'] > 0):
            self.buy_queue.upsert_stock(stock_code, stock_info["name"], stock_info["bidVol"][0], stock_info["bidPrice"][0], buy_premium - buy_premium_threshold, buy_premium,appraisal, 0,0, date_utils.get_current_millisecond())
        else:
            self.buy_queue.remove_stock(stock_code)
        # Print status updates once per minute
        if utils.should_print("strategy1", 60):
            logger.info("********************** Queue Status Start **********************")
            first_sell_queue = self.sell_queue.head
            first_buy_queue = self.buy_queue.head
            logger.info(f"\r\n{stock_code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            if first_sell_queue is not None:
                logger.info(
                    f"- Ask Queue: {first_sell_queue.name} ({first_sell_queue.code}), Appraisal: {first_sell_queue.appraisal}, Best Ask: {round(first_sell_queue.price, 4)}, Discount Rate: {round((Decimal(first_sell_queue.appraisal) - Decimal(first_sell_queue.price)) / Decimal(first_sell_queue.price) * Decimal(100), 4)}%, Premium Weight: {first_sell_queue.premium}, Value: {round(first_sell_queue.price * first_sell_queue.quantity * 100, 2)}, Hist Penalty: {first_sell_queue.history_penalty_rate}, Overheat Penalty: {first_sell_queue.overheating_penalty}\r\n")
            if first_buy_queue is not None:
                logger.info(
                    f"- Bid Queue: {first_buy_queue.name} ({first_buy_queue.code}), Appraisal: {first_buy_queue.appraisal}, Best Bid: {round(first_buy_queue.price, 4)}, Premium Rate: {round((Decimal(first_buy_queue.price) - Decimal(first_buy_queue.appraisal)) / Decimal(first_buy_queue.price) * Decimal(100), 4)}%, Premium Weight: {first_buy_queue.premium}, Value: {round(first_buy_queue.price * first_buy_queue.quantity * 100, 2)}\r\n")
            self.buy_queue.print_queue()
            logger.info("********************** Queue Status End **********************")
        # Prior to market close, print actual appraisal every 10 seconds for manual exits
        if utils.is_going_to_close() and utils.should_print("strategy1", 10):
            self.buy_queue.print_queue()
