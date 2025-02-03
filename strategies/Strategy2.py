# coding=utf-8
from xtquant import xtdata, xtconstant
import logging
import helper.data_loader as data_loader

logging.basicConfig(level=logging.INFO, format='%(message)s', filename='logs/app.log', filemode='a')
logger = logging.getLogger(__name__)

## T+0 高频交易策略
class Strategy2:
    def __init__(self, db, traderService):
        self.stock_T_0_codes = ["000002", "159980"]
        self.stock_T_0_infos = {}
        # 最大、小买入金额
        self.bid_max_money = 2200
        self.bid_min_money = 2000
        self.holding_money = 0
        self.db = db
        self.traderService = traderService
        data_loader.load_stock(self.db, self.stock_T_0_codes, self.stock_T_0_infos, self.traderService.get_holding())

    # 启动策略
    def run(self):
        subscribe_id = xtdata.subscribe_whole_quote(data_loader.convert_enhance_code(self.stock_T_0_codes),
                                                    callback=self.analyse)
        print(f"订阅结果: {subscribe_id}")

    # 拿到订阅数据，实时分析
    def analyse(self, msgs):
        is_holding = False
        for code in msgs:
            print(f"信息输出 code: {code}, {msgs[code]}")
            continue

            self.stock_T_0_infos[code].update({
                'askPrice': msgs[code]['askPrice'],
                'askVol': msgs[code]['askVol'],
                'bidPrice': msgs[code]['bidPrice'],
                'bidVol': msgs[code]['bidVol'],
                'status': True,
            })
            # 变化趋势数据记录下来给AI做训练，到第三版本使用
            is_holding = self.stock_T_0_infos[code]['hold_num'] > 0
            if is_holding:
                # todo 卖1数量如果是递减趋势，并且所剩数量仅占持有的2倍时卖出
                pass
            elif self.holding_money > 0:
                # todo 买1数量如果是递增趋势，并且所盛数量仅占最小买入额的2倍时买入
                pass

    # 做决策
    def make_decision(self):
        pass

    def buy(self, stock_id, num):
        pass

    def sell(self, stock_id, num):
        pass

    # 记录，后面数据统计复盘
    def record(self):
        pass