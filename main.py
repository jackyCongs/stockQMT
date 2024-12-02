# coding=utf-8
import math
import time
from decimal import Decimal, getcontext
from xtquant import xtdata
from db.db_pool import DBPool
import db.stock as stock_db
import helper.spider as spider
import helper.data_loader as data_loader
from helper import utils
from datetime import datetime, timedelta
from service import TraderService


# 全局变量
xtdata.enable_hello = False
db = DBPool()
# 等待被初始化的全局场内基金
inner_stock_infos = {}
stock_info = {}
# 等待被初始化的全局指数
target_index_infos = {}
index_info = {}
# 上一个交易日
yesterday = data_loader.get_previous_date()
# 单笔最大买入金额
max_bid_money = 5000
# 交易服务
traderService = TraderService.TraderService


def stock_handler(msgs):
    for code in msgs:
        print(f"订阅消息: stock-  {msgs[code]}")
        inner_stock_infos[code].update({
            'askPrice': msgs[code]['askPrice'],
            'askVol': msgs[code]['askVol'],
            'bidPrice': msgs[code]['bidPrice'],
            'bidVol': msgs[code]['bidVol'],
            'status': True,
        })
        #print(f"stock_handler-{inner_stock_infos[code]}")
        # 分析关联的code
        analysis_and_decision_mking(code)


def index_handler(msgs):
    for code in msgs:
        print(f"订阅消息: index-  {msgs[code]}")
        target_index_infos[utils.purified_code(code)].update({
            'start': msgs[code]['lastClose'],
            'current': msgs[code]['lastPrice'],
            'increase_rate': Decimal(round((msgs[code]['lastPrice'] - msgs[code]['lastClose']) / msgs[code]['lastClose'], 6)),
            'status': True,
        })
        # print(f"index_handler-{msgs[code]}")
        # 逐个分析关联的code
        for stock_code in target_index_infos[utils.purified_code(code)]['relation']:
            analysis_and_decision_mking(stock_code)


# 目前只考虑买，先不考虑卖的问题
def analysis_and_decision_mking(stock_code):
    global stock_info, index_info
    stock_info = inner_stock_infos[stock_code]
    index_info = target_index_infos[stock_info['target_index']]
    # 双方未就绪，不处理
    if index_info['status'] == False or stock_info['status'] == False:
        return

    if stock_info['last_net_worth_date'] != yesterday:
        return

    appraisal = Decimal(round(stock_info['last_net_worth'] * (Decimal(1) + index_info['increase_rate'] -
                                                      (stock_info['withdraw_commission_7rate'])), 6))
    # @todo 测试，把估值拉的高高的
    if stock_code == "160135.SZ":
        appraisal += Decimal(0.5)
    #print(f"{stock_code}-{appraisal}, ask:{stock_info['askPrice']}")
    # 当卖盘不为空，并且卖1出价小于估值时，进一步再判断溢价空间
    if len(stock_info['askPrice']) > 0 and stock_info['askPrice'][0] < appraisal:
        bid_price = 0
        bid_num = 0
        bid_money = 0
        premium_threshold = data_loader.get_premium(index_info['increase_rate'])
        print(premium_threshold)
        premium = 0
        for i, price in enumerate(stock_info['askPrice']):
            premium = round((appraisal - Decimal(price)) / Decimal(price) * 100, 4)
            if premium >= premium_threshold and bid_money <= max_bid_money:
                bid_price = round(price, 6)
                bid_num += stock_info['askVol'][i]
                bid_money += bid_price * bid_num * 100
                # 如果超过了最大单笔限上额，减去一点
                if bid_money > max_bid_money:
                    bid_num -= math.floor((bid_money - max_bid_money) / bid_price / 100)
        if bid_num > 0 and bid_price > 0 and stock_info['hold_status'] == 0:
            # 下单
            remark = f"买入日志: 买入{stock_code}, {datetime.now().strftime('%Y-%m-%d %H:%M:%S')},折价率: {premium}%，" \
                f"报价{bid_price},{bid_num}手, 目前卖盘{stock_info['askPrice']},{stock_info['askVol']}, 指数{index_info}"
            print(remark)
            order_seq = traderService.async_buy(stock_code, bid_price, bid_num, "折价策略", remark, inner_stock_infos)
            if order_seq:
                print(f"order_seq: {order_seq}")
            else:
                print("下单失败")


if __name__ == '__main__':
    while True:
        try:
            db.initialize_pool()
            data_loader.load_inner_stock(db, inner_stock_infos)
            data_loader.load_target_index(inner_stock_infos, target_index_infos)
            traderService = TraderService.TraderService()

            SId1 = xtdata.subscribe_whole_quote(data_loader.get_all_inner_stocks_code(db), callback=stock_handler)
            SId2 = xtdata.subscribe_whole_quote(data_loader.get_all_target_index_code(inner_stock_infos),
                                                callback=index_handler)
            print(f"订阅成功: {SId1}, {SId2}\r")
            xtdata.run()
        except Exception as e:
            print(e)
        finally:
            # 释放线程池
            db.close()
            traderService.close()
