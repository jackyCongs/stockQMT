# coding=utf-8

from xtquant import xtdata
from db.db_pool import DBPool
import db.stock as stock_db
import time


def handler(msgs):
    for code in msgs:
        print(msgs[code])


def init_stock(db_instance):
    stock_infos = []
    stocks = stock_db.get_stock_list(db_instance)
    # @todo 同步last_net_worth、last_net_worth_date、加载当天分红的情况
    # stock加载的数据结构
    # code、name、last_net_worth、last_net_worth_date、target_worth、
    #   withdraw_commission_7rate、买卖量价、持有数量、持有天数
    # 还需要加载分红的情况，如果有分红需要除权
    # 可买的价格 = (target_worth - 分红除权) * (1 + 目标指数的加权涨跌幅) * (1-withdraw_commission_7rate) * (溢价1-0.5) > 比卖价
    # 可卖的价格 = (target_worth - 分红除权) * (1 + 目标指数的涨跌幅) * (1-withdraw_commission_7rate) <= 买价
    for stock in stocks:
        print(stock)
        stock_infos[stock['code']] = {'code': stock['code'],
                                      'last_net_worth': stock['last_net_worth'],
                                      'last_net_worth_date': stock['last_net_worth_date'],
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
