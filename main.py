# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool
import db.stock as stock_db
import helper.spider as spider
import time
from datetime import datetime
from decimal import Decimal, getcontext

def handler(msgs):
    for code in msgs:
        print(msgs[code])


def init_stock(db_instance):
    stock_infos = []
    stocks = stock_db.get_stock_list(db_instance)
    # stock加载的数据结构
    # code、name、last_net_worth、last_net_worth_date、target_worth、
    #   withdraw_commission_7rate、买卖量价、持有数量、持有天数
    # 还需要加载分红的情况，如果有分红需要除权
    # 可买的价格 = (target_worth - 分红除权) * (1 + 目标指数的加权涨跌幅) * (1-withdraw_commission_7rate) * (溢价1-0.5) > 比卖价
    # 可卖的价格 = (target_worth - 分红除权) * (1 + 目标指数的涨跌幅) * (1-withdraw_commission_7rate) <= 买价
    for stock in stocks:
        print(stock)
        net_worth = spider.get_last_net_worth(stock['code'])
        if net_worth['code'] != 200:
            print(f"{stock['code']}, 获取基金净值信息失败: {net_worth['msg']}")
            continue
        if net_worth['bonus_date'] is not None and net_worth['bonus_date'] == datetime.now().strftime("%Y-%m-%d") \
                and net_worth['bonus_date'] != net_worth['bonus_date']:
            print(f"【{stock['code']}】今天有分红，每份除权{net_worth['bonus_money']}元")
            net_worth['net_worth'] = Decimal(net_worth['net_worth']) - Decimal(net_worth['bonus_money'])

        stock_infos[stock['code']] = {'code': stock['code'],
                                      'last_net_worth': Decimal(net_worth['net_worth']),
                                      'last_net_worth_date': net_worth['net_worth_date'],
                                      'withdraw_commission_7rate': stock['withdraw_commission_7rate'],
                                      }


if __name__ == '__main__':
    xtdata.enable_hello = False
    db_instance = DBPool()

    while True:
        try:
            db_instance.initialize_pool()
            init_stock(db_instance)
            SId = xtdata.subscribe_whole_quote(["161125.SZ", "161729.SZ"], callback=handler)
            print(SId)
            xtdata.run()
        except Exception as e:
            print(e)
        finally:
            # 释放线程池
            db_instance.close()
