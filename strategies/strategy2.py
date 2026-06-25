# coding=utf-8
import json

from xtquant import xtdata, xtconstant
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
        self.min_bid_amount = 1

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

        self.active_sell_orders = {}
        self.tick_size = 0.001
        self.active_sell_discount_threshold = 0.5

    def _get_lock(self, stock_code):
        # 如果stock_code对应的锁不存在，则创建一个新的锁
        if stock_code not in self.locks:
            self.locks[stock_code] = threading.Lock()
        return self.locks[stock_code]

    # 启动策略
    def run(self):
        data_loader.load_inner_stock(self.db, self.inner_stock_infos, self.strategy_etf_type)
        data_loader.load_target_index_for_etf(self.db, self.inner_stock_infos, self.target_index_infos, self.yesterday, self.strategy_etf_type)
        data_loader.fresh_holding(self.inner_stock_infos, self.target_index_infos, self.trader_service.get_holding())

        # 启动时恢复挂单状态
        try:
            hangings = self.trader_service.get_hanging()
            for order in hangings:
                if order.strategy_name == self.strategy_name and order.order_type == xtconstant.STOCK_SELL:
                    self.trader_strategy_service.active_sell_orders[order.stock_code] = order.order_id
                    logger.info(f"成功恢复已挂主动卖单：[{order.stock_code}] 订单ID: {order.order_id}, 价格: {order.price}")
        except Exception as e:
            logger.error(f"恢复挂单失败: {e}")

        group_codes = []
        for stock_code in self.inner_stock_infos:
            group_codes.append(stock_code)

        subscribe_id = xtdata.subscribe_whole_quote(group_codes, callback=self.handler)
        logging.info(f"subscribe successful: {subscribe_id}")
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
                    # print("未到开盘时间或已收盘")
                    return
                # begin to analysis data
                stock_info = self.inner_stock_infos[code]
                index_info = self.target_index_infos[data_loader.get_group_code(stock_info['target_index'], code)]
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
                    if get_time() - stock_info['timestamp'] >= 600:
                        logger.error(f"stock{code} 更新时间异常，{get_time() - stock_info['timestamp']}秒未更新")
                        self.premium_manager.buy_queue.remove_stock(code)
                        # logger.info(stock_info)
                    continue
                if stock_info['last_net_worth_date'] != self.yesterday:
                    logger.warning(f"last_net_worth_date异常: {stock_info['last_net_worth_date']} - {self.yesterday}")
                    continue
                # maintain a premium queue
                stock_info['last_net_worth'] = float(stock_info['last_net_worth'])

                self.premium_manager.update(code, stock_info, index_info, self.realtime_iopv_infos[utils.purified_code(code)])

                # 主动挂单与被动成交优化逻辑
                # 获取当前股票代码在交易服务中对应的主动卖单（Active Sell Order）的详细信息
                active_order = self.trader_strategy_service.get_active_sell_order_details(code)
                # 计算该主动卖单中尚未成交的股数（如果存在该订单，为总委托量减去已成交量，否则为 0）
                active_shares = (active_order.order_volume - active_order.traded_volume) if active_order else 0
                # 将未成交的股数转换为“手”（每手等于 100 股）
                active_lots = active_shares // 100
                # 计算当前总计可用于卖出的手数（当前可用持仓 + 已挂单待成交的股票手数）
                total_available_lots = stock_info['hold_can_use_num'] + active_lots

                # 如果有可用持仓或已挂单待成交的股票，说明可以进行卖出逻辑的处理
                if total_available_lots > 0
                    # 从溢价管理器的买入队列中尝试获取该股票的买入节点信息（检查是否有买盘挂单匹配）
                    buy_node = self.premium_manager.buy_queue.code_map.get(code)
                    # 判断当前市场中是否存在合适的买盘（买盘节点不为空，且溢价率非负，表明买方报价高于或等于评估净值）
                    is_suitable_buy_1 = (buy_node is not None and buy_node.premium >= 0)

                    # 如果存在合适的买盘（满足即时被动成交条件）
                    if is_suitable_buy_1:
                        # 如果当前存在已挂出的小幅加价限价单
                        if active_order:
                            # 异步提交撤单并卖出的任务：先撤掉原有的挂单，然后以买盘价格进行即时卖出成交
                            self.trader_strategy_service.processor.submit_task(
                                self.trader_strategy_service.cancel_and_sell_task, code, active_order.order_id, buy_node.price, buy_node.appraisal, stock_info, self.inner_stock_infos, self.target_index_infos
                            )
                        # 如果当前没有挂出限价单，直接进行即时卖出
                        else:
                            # 获取当前可用的全部持仓股数作为卖出数量
                            sell_num = stock_info['hold_can_use_num']
                            # 从买入队列中移出该股票，避免重复触发
                            self.premium_manager.buy_queue.remove_stock(code)
                            # 执行卖出操作，将股票以买盘节点价格卖给对手盘
                            self.trader_strategy_service.to_sell(
                                self.inner_stock_infos, self.target_index_infos, code, buy_node.price, buy_node.appraisal, True
                            )
                            # 在本地持仓数据中扣除对应的卖出股数
                            stock_info['hold_can_use_num'] -= sell_num
                    # 如果不存在满足即时成交条件的合适买盘，则执行主动挂单逻辑
                    else:
                        # 确保当前盘口有卖一价，且卖一价有效
                        if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0]:
                            # 获取该股票当前的实时评估净值 (IOPV)
                            appraisal = self.realtime_iopv_infos[utils.purified_code(code)]['current']
                            # 确认实时估值有效且大于 0
                            if appraisal and appraisal > 0:
                                # 取当前的卖一价
                                ask_price_1 = stock_info['askPrice'][0]
                                # 计算目标挂单价格：在当前卖一价的基础之上加 1 个最小变动单位 (Tick)
                                target_price = ask_price_1 + self.tick_size
                                # 计算目标挂单价格相对于评估净值的折价比例
                                discount = (appraisal - target_price) / appraisal * 100
                                # 如果折价幅度在限定的阈值之内（例如低于 0.5%，说明折价亏损很小，可以接受更低价格挂单）
                                if discount <= self.active_sell_discount_threshold:
                                    # 将目标价格设为卖一价（直接排在卖一最前列）
                                    desired_price = ask_price_1
                                # 如果折价幅度超过了限制（说明若以卖一价卖出会亏损偏多）
                                else:
                                    # 挂在卖一价之上的一个 tick (即卖一价 + 1 tick)
                                    desired_price = target_price

                                # 如果当前已经有正在挂着的主动卖单
                                if active_order:
                                    # 计算当前已有挂单的价格与我们新计算的期望挂单价格的差值
                                    price_diff = abs(active_order.price - desired_price)
                                    # 检查挂单数量是否与我们目前总的可用卖出数量（手 * 100）不一致
                                    vol_mismatch = (active_shares != total_available_lots * 100)
                                    # 如果挂单价格有偏差，或者挂单数量与最新可用数量不匹配
                                    if price_diff > 1e-5 or vol_mismatch:
                                        # 异步提交撤销旧单并重新以新价格/数量挂单的任务
                                        self.trader_strategy_service.processor.submit_task(
                                            self.trader_strategy_service.cancel_and_place_active_sell_task, code, active_order.order_id, desired_price, total_available_lots, stock_info, self.inner_stock_infos
                                        )
                                # 如果当前没有任何挂单
                                else:
                                    # 异步提交直接以期望价格挂出主动卖单的任务
                                    self.trader_strategy_service.processor.submit_task(
                                        self.trader_strategy_service.place_active_sell_task, code, desired_price, total_available_lots, stock_info, self.inner_stock_infos
                                    )
                    # 处理完该股票的卖出/挂单逻辑后，跳过本轮循环中后续的代码，继续处理下一只股票
                    continue

                # it's the time to design trading part
                first_buy_queue_node = self.premium_manager.buy_queue.head
                first_sell_queue_node = self.premium_manager.sell_queue.head

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
