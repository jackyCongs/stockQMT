# coding=utf-8

#from xtquant import xtdata
from db.db_pool import DBPool
import db.stock as stock_db
import time


def handler(msgs):
    for code in msgs:
        print(msgs[code])


def init_stock(db_instance):
    stocks = stock_db.get_stock_list(db_instance)
    for stock in stocks:
        print(stock)


if __name__ == '__main__':
    #xtdata.enable_hello = False
    db_instance = DBPool()

    while True:
        try:
            db_instance.initialize_pool()
            init_stock(db_instance)

            # SId = xtdata.subscribe_whole_quote(["161125.SZ", "161729.SZ"], callback=handler)
            # print(SId)
            # xtdata.run()
            time.sleep(100)
        except Exception as e:
            print(e)
        finally:
            # 释放线程池
            db_instance.close()
