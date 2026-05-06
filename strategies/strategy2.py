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
from helper import utils, date_utils, notifier, spider
from service.watchdog_service import WatchdogService

logger = logging.getLogger(__name__)

# 没什么用，主要记录一下当前订阅的stock code
stock_codes = ['159106.SZ', '159107.SZ', '159108.SZ', '159122.SZ', '159123.SZ', '159129.SZ', '159133.SZ', '159136.SZ', '159150.SZ', '159203.SZ', '159205.SZ', '159206.SZ', '159207.SZ', '159208.SZ', '159209.SZ', '159211.SZ', '159212.SZ', '159213.SZ', '159215.SZ', '159216.SZ', '159218.SZ', '159219.SZ', '159226.SZ', '159227.SZ', '159228.SZ', '159230.SZ', '159231.SZ', '159238.SZ', '159240.SZ', '159241.SZ', '159242.SZ', '159243.SZ', '159246.SZ', '159248.SZ', '159249.SZ', '159253.SZ', '159255.SZ', '159256.SZ', '159257.SZ', '159258.SZ', '159259.SZ', '159260.SZ', '159261.SZ', '159263.SZ', '159267.SZ', '159270.SZ', '159272.SZ', '159275.SZ', '159278.SZ', '159279.SZ', '159283.SZ', '159287.SZ', '159288.SZ', '159289.SZ', '159290.SZ', '159291.SZ', '159292.SZ', '159295.SZ', '159296.SZ', '159298.SZ', '159299.SZ', '159300.SZ', '159301.SZ', '159305.SZ', '159306.SZ', '159307.SZ', '159310.SZ', '159311.SZ', '159315.SZ', '159320.SZ', '159321.SZ', '159322.SZ', '159325.SZ', '159326.SZ', '159327.SZ', '159328.SZ', '159330.SZ', '159332.SZ', '159336.SZ', '159337.SZ', '159338.SZ', '159339.SZ', '159350.SZ', '159351.SZ', '159352.SZ', '159353.SZ', '159355.SZ', '159356.SZ', '159357.SZ', '159358.SZ', '159359.SZ', '159360.SZ', '159361.SZ', '159362.SZ', '159363.SZ', '159365.SZ', '159367.SZ', '159368.SZ', '159369.SZ', '159370.SZ', '159371.SZ', '159372.SZ', '159373.SZ', '159375.SZ', '159376.SZ', '159377.SZ', '159378.SZ', '159379.SZ', '159380.SZ', '159381.SZ', '159382.SZ', '159383.SZ', '159385.SZ', '159386.SZ', '159387.SZ', '159388.SZ', '159389.SZ', '159390.SZ', '159391.SZ', '159392.SZ', '159393.SZ', '159500.SZ', '159505.SZ', '159507.SZ', '159508.SZ', '159510.SZ', '159511.SZ', '159512.SZ', '159515.SZ', '159516.SZ', '159517.SZ', '159520.SZ', '159521.SZ', '159523.SZ', '159525.SZ', '159526.SZ', '159527.SZ', '159528.SZ', '159530.SZ', '159531.SZ', '159532.SZ', '159533.SZ', '159535.SZ', '159537.SZ', '159538.SZ', '159539.SZ', '159540.SZ', '159541.SZ', '159542.SZ', '159543.SZ', '159546.SZ', '159547.SZ', '159549.SZ', '159550.SZ', '159551.SZ', '159552.SZ', '159553.SZ', '159555.SZ', '159556.SZ', '159558.SZ', '159559.SZ', '159560.SZ', '159562.SZ', '159563.SZ', '159565.SZ', '159566.SZ', '159571.SZ', '159572.SZ', '159573.SZ', '159575.SZ', '159576.SZ', '159578.SZ', '159581.SZ', '159582.SZ', '159583.SZ', '159586.SZ', '159587.SZ', '159588.SZ', '159589.SZ', '159590.SZ', '159591.SZ', '159592.SZ', '159593.SZ', '159595.SZ', '159596.SZ', '159597.SZ', '159599.SZ', '159605.SZ', '159606.SZ', '159607.SZ', '159608.SZ', '159609.SZ', '159610.SZ', '159611.SZ', '159613.SZ', '159616.SZ', '159617.SZ', '159618.SZ', '159619.SZ', '159620.SZ', '159622.SZ', '159623.SZ', '159625.SZ', '159627.SZ', '159628.SZ', '159629.SZ', '159631.SZ', '159633.SZ', '159635.SZ', '159637.SZ', '159638.SZ', '159639.SZ', '159640.SZ', '159641.SZ', '159642.SZ', '159643.SZ', '159645.SZ', '159647.SZ', '159650.SZ', '159652.SZ', '159653.SZ', '159656.SZ', '159657.SZ', '159658.SZ', '159661.SZ', '159662.SZ', '159663.SZ', '159665.SZ', '159666.SZ', '159667.SZ', '159669.SZ', '159670.SZ', '159671.SZ', '159672.SZ', '159673.SZ', '159675.SZ', '159676.SZ', '159677.SZ', '159678.SZ', '159679.SZ', '159680.SZ', '159681.SZ', '159682.SZ', '159685.SZ', '159686.SZ', '159689.SZ', '159690.SZ', '159692.SZ', '159695.SZ', '159697.SZ', '159698.SZ', '159701.SZ', '159703.SZ', '159706.SZ', '159707.SZ', '159708.SZ', '159709.SZ', '159713.SZ', '159715.SZ', '159716.SZ', '159717.SZ', '159719.SZ', '159720.SZ', '159721.SZ', '159723.SZ', '159725.SZ', '159728.SZ', '159729.SZ', '159730.SZ', '159731.SZ', '159732.SZ', '159736.SZ', '159738.SZ', '159739.SZ', '159743.SZ', '159745.SZ', '159748.SZ', '159752.SZ', '159755.SZ', '159757.SZ', '159758.SZ', '159760.SZ', '159761.SZ', '159763.SZ', '159766.SZ', '159767.SZ', '159768.SZ', '159770.SZ', '159773.SZ', '159775.SZ', '159777.SZ', '159778.SZ', '159779.SZ', '159786.SZ', '159787.SZ', '159790.SZ', '159791.SZ', '159793.SZ', '159796.SZ', '159797.SZ', '159798.SZ', '159800.SZ', '159801.SZ', '159804.SZ', '159805.SZ', '159806.SZ', '159807.SZ', '159808.SZ', '159810.SZ', '159811.SZ', '159812.SZ', '159813.SZ', '159814.SZ', '159819.SZ', '159820.SZ', '159821.SZ', '159822.SZ', '159824.SZ', '159825.SZ', '159827.SZ', '159828.SZ', '159830.SZ', '159831.SZ', '159834.SZ', '159835.SZ', '159836.SZ', '159837.SZ', '159838.SZ', '159840.SZ', '159841.SZ', '159842.SZ', '159843.SZ', '159845.SZ', '159847.SZ', '159848.SZ', '159849.SZ', '159851.SZ', '159852.SZ', '159855.SZ', '159856.SZ', '159857.SZ', '159858.SZ', '159859.SZ', '159861.SZ', '159862.SZ', '159863.SZ', '159864.SZ', '159865.SZ', '159867.SZ', '159869.SZ', '159870.SZ', '159871.SZ', '159872.SZ', '159873.SZ', '159875.SZ', '159876.SZ', '159877.SZ', '159880.SZ', '159881.SZ', '159883.SZ', '159885.SZ', '159886.SZ', '159887.SZ', '159888.SZ', '159889.SZ', '159890.SZ', '159891.SZ', '159895.SZ', '159896.SZ', '159898.SZ', '159899.SZ', '159901.SZ', '159902.SZ', '159903.SZ', '159905.SZ', '159906.SZ', '159907.SZ', '159908.SZ', '159909.SZ', '159910.SZ', '159913.SZ', '159915.SZ', '159916.SZ', '159918.SZ', '159919.SZ', '159922.SZ', '159923.SZ', '159925.SZ', '159928.SZ', '159929.SZ', '159930.SZ', '159931.SZ', '159933.SZ', '159934.SZ', '159935.SZ', '159936.SZ', '159937.SZ', '159938.SZ', '159939.SZ', '159940.SZ', '159943.SZ', '159944.SZ', '159945.SZ', '159948.SZ', '159949.SZ', '159952.SZ', '159954.SZ', '159956.SZ', '159957.SZ', '159958.SZ', '159959.SZ', '159961.SZ', '159964.SZ', '159965.SZ', '159966.SZ', '159967.SZ', '159968.SZ', '159969.SZ', '159970.SZ', '159971.SZ', '159973.SZ', '159974.SZ', '159975.SZ', '159976.SZ', '159977.SZ', '159980.SZ', '159981.SZ', '159982.SZ', '159985.SZ', '159991.SZ', '159992.SZ', '159993.SZ', '159994.SZ', '159995.SZ', '159996.SZ', '159997.SZ', '159998.SZ', '160119.SZ', '161907.SZ', '510010.SH', '510020.SH', '510030.SH', '510040.SH', '510050.SH', '510060.SH', '510090.SH', '510100.SH', '510130.SH', '510150.SH', '510160.SH', '510170.SH', '510180.SH', '510190.SH', '510200.SH', '510210.SH', '510230.SH', '510270.SH', '510290.SH', '510300.SH', '510310.SH', '510320.SH', '510330.SH', '510350.SH', '510360.SH', '510370.SH', '510380.SH', '510390.SH', '510410.SH', '510500.SH', '510510.SH', '510530.SH', '510550.SH', '510560.SH', '510570.SH', '510580.SH', '510590.SH', '510600.SH', '510630.SH', '510650.SH', '510660.SH', '510670.SH', '510680.SH', '510710.SH', '510720.SH', '510760.SH', '510770.SH', '510800.SH', '510810.SH', '510850.SH', '510880.SH', '510950.SH', '510990.SH', '511360.SH', '512000.SH', '512010.SH', '512020.SH', '512030.SH', '512040.SH', '512050.SH', '512060.SH', '512070.SH', '512080.SH', '512090.SH', '512100.SH', '512120.SH', '512150.SH', '512160.SH', '512170.SH', '512180.SH', '512190.SH', '512200.SH', '512220.SH', '512240.SH', '512250.SH', '512260.SH', '512290.SH', '512330.SH', '512360.SH', '512370.SH', '512380.SH', '512390.SH', '512400.SH', '512480.SH', '512500.SH', '512510.SH', '512520.SH', '512530.SH', '512550.SH', '512560.SH', '512570.SH', '512580.SH', '512600.SH', '512620.SH', '512630.SH', '512640.SH', '512660.SH', '512670.SH', '512680.SH', '512690.SH', '512700.SH', '512710.SH', '512720.SH', '512730.SH', '512750.SH', '512760.SH', '512770.SH', '512800.SH', '512810.SH', '512820.SH', '512870.SH', '512880.SH', '512890.SH', '512900.SH', '512910.SH', '512930.SH', '512950.SH', '512960.SH', '512970.SH', '512980.SH', '512990.SH', '513220.SH', '513310.SH', '513360.SH', '515000.SH', '515010.SH', '515020.SH', '515030.SH', '515050.SH', '515060.SH', '515070.SH', '515080.SH', '515090.SH', '515100.SH', '515110.SH', '515120.SH', '515130.SH', '515150.SH', '515160.SH', '515170.SH', '515180.SH', '515190.SH', '515200.SH', '515210.SH', '515220.SH', '515230.SH', '515250.SH', '515260.SH', '515290.SH', '515300.SH', '515320.SH', '515330.SH', '515350.SH', '515360.SH', '515370.SH', '515380.SH', '515390.SH', '515400.SH', '515450.SH', '515530.SH', '515550.SH', '515560.SH', '515580.SH', '515590.SH', '515600.SH', '515630.SH', '515650.SH', '515660.SH', '515680.SH', '515700.SH', '515710.SH', '515720.SH', '515730.SH', '515750.SH', '515760.SH', '515770.SH', '515790.SH', '515800.SH', '515810.SH', '515850.SH', '515860.SH', '515880.SH', '515890.SH', '515900.SH', '515910.SH', '515920.SH', '515950.SH', '515960.SH', '515980.SH', '516000.SH', '516010.SH', '516020.SH', '516050.SH', '516060.SH', '516070.SH', '516080.SH', '516090.SH', '516100.SH', '516110.SH', '516120.SH', '516130.SH', '516150.SH', '516160.SH', '516180.SH', '516190.SH', '516200.SH', '516210.SH', '516220.SH', '516250.SH', '516260.SH', '516270.SH', '516290.SH', '516300.SH', '516310.SH', '516320.SH', '516330.SH', '516350.SH', '516360.SH', '516380.SH', '516390.SH', '516500.SH', '516510.SH', '516520.SH', '516530.SH', '516550.SH', '516560.SH', '516570.SH', '516580.SH', '516590.SH', '516600.SH', '516610.SH', '516620.SH', '516630.SH', '516640.SH', '516650.SH', '516660.SH', '516670.SH', '516700.SH', '516710.SH', '516720.SH', '516730.SH', '516750.SH', '516760.SH', '516770.SH', '516780.SH', '516790.SH', '516800.SH', '516810.SH', '516820.SH', '516830.SH', '516850.SH', '516860.SH', '516880.SH', '516890.SH', '516900.SH', '516910.SH', '516920.SH', '516930.SH', '516950.SH', '516960.SH', '516970.SH', '516980.SH', '517050.SH', '517090.SH', '517110.SH', '517120.SH', '517160.SH', '517180.SH', '517200.SH', '517330.SH', '517380.SH', '517390.SH', '517400.SH', '517520.SH', '517660.SH', '517770.SH', '517800.SH', '517850.SH', '517880.SH', '517900.SH', '518600.SH', '518660.SH', '518680.SH', '518800.SH', '518850.SH', '518860.SH', '518880.SH', '518890.SH', '530000.SH', '530050.SH', '530080.SH', '530100.SH', '530180.SH', '530280.SH', '530300.SH', '530380.SH', '530530.SH', '530580.SH', '530680.SH', '530800.SH', '530880.SH', '560000.SH', '560010.SH', '560030.SH', '560060.SH', '560070.SH', '560080.SH', '560090.SH', '560100.SH', '560150.SH', '560170.SH', '560180.SH', '560190.SH', '560220.SH', '560260.SH', '560280.SH', '560300.SH', '560330.SH', '560350.SH', '560360.SH', '560380.SH', '560500.SH', '560510.SH', '560520.SH', '560530.SH', '560550.SH', '560560.SH', '560570.SH', '560580.SH', '560590.SH', '560610.SH', '560620.SH', '560630.SH', '560650.SH', '560660.SH', '560680.SH', '560690.SH', '560700.SH', '560750.SH', '560770.SH', '560780.SH', '560800.SH', '560810.SH', '560820.SH', '560850.SH', '560860.SH', '560880.SH', '560890.SH', '560900.SH', '560980.SH', '560990.SH', '561000.SH', '561010.SH', '561060.SH', '561090.SH', '561100.SH', '561120.SH', '561130.SH', '561160.SH', '561170.SH', '561180.SH', '561190.SH', '561200.SH', '561220.SH', '561230.SH', '561260.SH', '561280.SH', '561300.SH', '561310.SH', '561320.SH', '561330.SH', '561350.SH', '561360.SH', '561370.SH', '561380.SH', '561500.SH', '561510.SH', '561550.SH', '561560.SH', '561570.SH', '561580.SH', '561590.SH', '561600.SH', '561660.SH', '561680.SH', '561700.SH', '561750.SH', '561760.SH', '561770.SH', '561780.SH', '561790.SH', '561800.SH', '561880.SH', '561900.SH', '561910.SH', '561920.SH', '561930.SH', '561950.SH', '561960.SH', '561980.SH', '561990.SH', '562000.SH', '562010.SH', '562030.SH', '562050.SH', '562070.SH', '562300.SH', '562310.SH', '562320.SH', '562330.SH', '562340.SH', '562350.SH', '562360.SH', '562380.SH', '562390.SH', '562500.SH', '562510.SH', '562520.SH', '562530.SH', '562550.SH', '562560.SH', '562570.SH', '562580.SH', '562590.SH', '562600.SH', '562660.SH', '562700.SH', '562800.SH', '562810.SH', '562820.SH', '562850.SH', '562860.SH', '562870.SH', '562880.SH', '562890.SH', '562900.SH', '562910.SH', '562920.SH', '562930.SH', '562950.SH', '562960.SH', '562970.SH', '562990.SH', '563010.SH', '563020.SH', '563030.SH', '563050.SH', '563060.SH', '563080.SH', '563090.SH', '563150.SH', '563180.SH', '563200.SH', '563210.SH', '563220.SH', '563230.SH', '563300.SH', '563320.SH', '563330.SH', '563350.SH', '563360.SH', '563380.SH', '563500.SH', '563510.SH', '563520.SH', '563530.SH', '563550.SH', '563570.SH', '563590.SH', '563600.SH', '563630.SH', '563650.SH', '563660.SH', '563670.SH', '563690.SH', '563700.SH', '563790.SH', '563800.SH', '563850.SH', '563860.SH', '563890.SH', '563930.SH', '563980.SH']

print_count_index = 0
rest_index_push_count = 0
class Strategy2:
    def __init__(self, db, trader_service, platform, cookie):
        self.frozen_amount = 0
        self.bought_list = {}
        self.stock_list = []
        # 等待被初始化的全局场内基金
        self.inner_stock_infos = {}
        # 等待被初始化的全局指数
        self.target_index_infos = {}
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
        data_loader.load_target_index(self.db, self.inner_stock_infos, self.target_index_infos, self.yesterday)
        data_loader.fresh_holding(self.inner_stock_infos, self.target_index_infos, self.trader_service.get_holding())

        for index_code in self.target_index_infos:
            group_codes = self.target_index_infos[index_code]['relation'].copy()
            group_codes.append(utils.enhance_stock_code(index_code, 'index'))
            subscribe_id = xtdata.subscribe_whole_quote(group_codes, callback=self.handler)
            logging.info(f"subscribe successful: {subscribe_id}, {index_code}")
            self.watchdog.register(f"s2_group_{index_code}", 180, "策略2-indexGroup行情")

        time.sleep(5)
        logger.info(f"loading rest index...")
        rest_index_codes = data_loader.get_rest_index(self.target_index_infos)
        logger.info(f"total target index nums: {len(self.target_index_infos)}")
        logger.info(f"rest_index_codes nums: {len(rest_index_codes)}, {rest_index_codes}")
        # 异步多线程通过第三方订阅没有检测到的指数信息
        self.subscribe_rest_index_stock(rest_index_codes)
        self.watchdog.start()
        time.sleep(10)
        self.completed_loading = True
        threading.Thread(target=data_loader.interval_fresh_holding, args=(self.inner_stock_infos, self.target_index_infos, self.trader_service)).start()

    def handler(self, msgs):
        global print_count_index
        t0 = time.perf_counter()
        try:
            target_index = None
            for code in list(msgs):
                # code有可能被pop删除了，所以需要在这验证一下
                if code not in msgs:
                    continue
                #先处理指数，如果第一个就是指数
                if utils.is_target_index(code) and target_index is None:
                    target_index = utils.purified_code(code)
                    self.handle_index(msgs[code], target_index)
                    self.watchdog.feed(f"s2_group_{target_index}")
                    continue
                #先处理指数，如果第一个不是指数
                if not utils.is_target_index(code) and target_index is None:
                    target_index = self.inner_stock_infos[code]['target_index']
                    target_index_suffix = utils.enhance_stock_code(target_index, 'index')
                    self.watchdog.feed(f"s2_group_{target_index}")
                    if target_index_suffix in msgs:
                        self.handle_index(msgs[target_index_suffix], target_index)
                        msgs.pop(target_index_suffix)
                        continue
                self.handle_stock(msgs[code], code)

                if not utils.is_normal_trading_hours():
                    # print("未到开盘时间或已收盘")
                    return
                # begin to analysis data
                stock_info = self.inner_stock_infos[code]
                index_info = self.target_index_infos[stock_info['target_index']]
                if index_info['status'] == False or stock_info['status'] == False:
                    if self.completed_loading:
                        logger.warning(f"状态未就绪:")
                        # logger.warning(stock_info)
                        logger.warning(f"target_index: {target_index}")
                        logger.warning(index_info)
                    continue

                # 如果更新时间超过1秒就不处理了
                if (get_time() - index_info['timestamp'] > 8) or (get_time() - stock_info['timestamp'] > 8):
                    if target_index in self.premium_manager.sell_queue:
                        self.premium_manager.sell_queue[target_index].remove_stock(code)
                    # if target_index in self.premium_manager.buy_queue:
                    #     self.premium_manager.buy_queue[target_index].remove_stock(code)
                    # 超过10秒必然是异常，需要提示出来
                    if get_time() - index_info['timestamp'] >= 60:
                        logger.error(f"index{target_index} 更新时间异常，{get_time() - index_info['timestamp']}秒未更新")
                        self.premium_manager.buy_queue[target_index].remove_stock(code)
                        logger.info(index_info)
                    if get_time() - stock_info['timestamp'] >= 60:
                        logger.error(f"stock{code} 更新时间异常，{get_time() - stock_info['timestamp']}秒未更新")
                        self.premium_manager.buy_queue[target_index].remove_stock(code)
                        logger.info(stock_info)
                    continue
                if stock_info['last_net_worth_date'] != self.yesterday:
                    logger.warning(f"last_net_worth_date异常: {stock_info['last_net_worth_date']} - {self.yesterday}")
                    continue
                # maintain a premium queue
                stock_info['last_net_worth'] = float(stock_info['last_net_worth'])
                index_info['increase_rate'] = float(index_info['increase_rate'])

                appraisal = round(stock_info['last_net_worth'] * (1 + index_info['increase_rate'] * 0.95), 6)

                self.premium_manager.update(appraisal, code, target_index, stock_info, index_info)
                # it's the time to design trading part
                first_buy_queue_node = self.premium_manager.buy_queue[target_index].head
                first_sell_queue_node = self.premium_manager.sell_queue[target_index].head
                # check whether is can be sold
                if first_buy_queue_node is not None and first_buy_queue_node.code == code and first_buy_queue_node.premium >= 0:
                    logger.info(f"prepare to sell {code}")
                    self.premium_manager.buy_queue[target_index].remove_stock(code)
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
                    if first_sell_queue_node is not None and first_sell_queue_node.code == code and self.is_max_premium(code) and first_sell_queue_node.premium >= 0:
                        logger.info(f"prepare to buy {code} {get_datetime().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                        self.premium_manager.sell_queue[target_index].remove_stock(code)
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
                    if (date_utils.get_current_millisecond() - first_sell_queue_node.update_time) / 1000 > 1:
                        self.premium_manager.sell_queue[target_index].remove_stock(first_sell_queue_node.code)
                        continue
                    if (date_utils.get_current_millisecond() - first_buy_queue_node.update_time) / 1000 > 1:
                        self.premium_manager.sell_queue[target_index].remove_stock(first_buy_queue_node.code)
                        continue
                    if ((first_sell_queue_node.premium > abs(first_buy_queue_node.premium) + self.base_premium_threshold) and
                            (first_sell_queue_node.quantity * first_sell_queue_node.price >= self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] * first_buy_queue_node.price) and
                            self.inner_stock_infos[first_buy_queue_node.code]['hold_can_use_num'] > 0 and first_buy_queue_node.premium > -self.base_premium_threshold):
                        # 操作之前，先从队列中移出去
                        self.premium_manager.buy_queue[target_index].remove_stock(first_buy_queue_node.code)
                        self.premium_manager.sell_queue[target_index].remove_stock(first_sell_queue_node.code)
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
                logging.info(f'{datetime.now()} main函数运行耗时 {(time.perf_counter() - t0) * 1000:.3f} ms, 处理订阅任务数量: {len(msgs)}个')

    def handle_index(self, index_tick, index_code):
        # print(f"qmt time: {datetime.fromtimestamp(index_tick['time'] / 1000).strftime('%H:%M:%S')}, local time: {get_datetime()}, 落后: {get_time() - index_tick['time'] / 1000}")
        try:
            if index_tick['lastClose'] == 0:
                self.target_index_infos[index_code].update({'status': True})
                return
            if datetime.fromtimestamp(index_tick['time'] / 1000).strftime('%H:%M:%S') == "00:00:00":
                if self.target_index_infos[index_code]['status']:
                    self.target_index_infos[index_code].update({'status': False})
                return

            self.target_index_infos[index_code].update({
                'timestamp': index_tick['time'] / 1000,
                'time': datetime.fromtimestamp(index_tick['time'] / 1000).strftime('%H:%M:%S'),
                'start': index_tick['lastClose'],
                'current': index_tick['lastPrice'],
                'increase_rate': round((index_tick['lastPrice'] - index_tick['lastClose']) / index_tick['lastClose'], 6),
                'data': index_tick,
                'status': True,
            })
        except Exception as e:
            logger.exception(f"Stock Index handler CRASHED: {e}")
            notifier.send_telegram_alert("报警", f"{self.strategy_name}策略, handle_index中发生致命错误: {str(e)[:200]},\n请立即处理")

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

    def is_max_premium(self, stock_code):
        max_premium = 0
        max_stock_code = None
        for index_code in self.premium_manager.sell_queue:
            if self.premium_manager.sell_queue[index_code] is None:
                continue
            if self.premium_manager.sell_queue[index_code].head is None:
                continue
            if self.premium_manager.sell_queue[index_code].head.premium > max_premium:
                max_premium = self.premium_manager.sell_queue[index_code].head.premium
                max_stock_code = self.premium_manager.sell_queue[index_code].head.code
        return max_stock_code == stock_code

    def subscribe_rest_index_stock(self, rest_index_codes):
        for index_code in rest_index_codes:
            threading.Thread(target=spider.stream_listener, args=(index_code, self.spider_cookie, self.subscribe_detail_index_stock)).start()
            time.sleep(0.5)

    def subscribe_detail_index_stock(self, line, index_code):
        global rest_index_push_count
        try:
            data = json.loads(line.replace('data: ', ''))
            if data['data'] == "null" or data['data'] is None:
                # logger.info(f"{index_code}: {data['data']}")
                return
            # 记录last day数据
            if 'f60' in data['data']:
                self.target_index_infos[index_code].update({'start': data['data']['f60']})
            if 'f43' not in data['data']:
                # logger.warning(f"{index_code} [subscribe_detail_index_stock] 发生错误，关键ke数据不存在- {line}")
                return
            current_time = 0
            if 'f86' in data['data']:
                current_time = data['data']['f86']
            resp = {'current_index': data['data']['f43']}
            current_index = resp['current_index']
            current_time_formate = datetime.fromtimestamp(current_time).strftime('%H:%M:%S')
            if current_index <= 0:
                return

            # print(f"third time: {current_time_formate}, local time: {get_datetime()}, 落后: {get_time() - current_time}")
            self.target_index_infos[index_code].update({
                # 只有从这里更新的指数数据有这个key，防止连接中断后依据死数据做决策
                'time': current_time_formate,
                'timestamp': current_time,
                'current': resp['current_index'],
                'increase_rate': round((resp['current_index'] - self.target_index_infos[index_code]['start']) / self.target_index_infos[index_code]['start'], 6),
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